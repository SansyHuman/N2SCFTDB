"""Check the native cache against schema-1 Python data and independent weights.

Build the optional extension with ``python index/cpp/build_char_cache.py`` first.
Every database and fake LiE executable used here lives in a temporary directory.
"""

from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    native = importlib.import_module("index._char_cache_cpp")
except ModuleNotFoundError as exc:
    if exc.name != "index._char_cache_cpp":
        raise
    native = None


def load_backend(name):
    """Keep both public implementations alive without reloading their globals."""
    module_name = f"index._test_char_cache_{name}"
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / "index/char_decomposition_cache.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"N2SCFTDB_CHAR_CACHE_BACKEND": name}):
        spec.loader.exec_module(module)
    return module


def su2_weights(powers):
    """Independent SU(2) weight expansion, followed by highest-weight subtraction."""
    weights = {0: 1}
    for adams, exponent in enumerate(powers, 1):
        for _ in range(exponent):
            result = {}
            for weight, coefficient in weights.items():
                for shift in (-adams, adams):
                    result[weight + shift] = result.get(weight + shift, 0) + coefficient
            weights = result
    return {(n,): weights.get(n, 0) - weights.get(n + 2, 0)
            for n in range(max(weights) + 1)
            if weights.get(n, 0) != weights.get(n + 2, 0)}


def fork_cache_read(cache, connection):
    try:
        connection.send(("ok", cache.get_decomposition("A1", (2,), (1,))))
        cache.close()
    except BaseException as exc:
        connection.send(("error", repr(exc)))
    finally:
        connection.close()


@unittest.skipIf(native is None, "build index._char_cache_cpp to test the native cache")
class NativeCharacterCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = load_backend("python")
        cls.cpp = load_backend("cpp")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="n2-native-character-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "characters.db"

    def cache(self, **kwargs):
        cache = native.CharacterDecompositionCache(str(self.path), **kwargs)
        self.addCleanup(cache.close)
        return cache

    def public_cache(self, backend, **kwargs):
        cache = backend.CharacterDecompositionCache(
            database_path=self.path, max_workers=1, **kwargs
        )
        self.addCleanup(cache.close)
        return cache

    def fake_lie(self, source):
        executable = self.directory / "fake lie executable"
        executable.write_text(f"#!{sys.executable}\n" + source)
        executable.chmod(0o700)
        return str(executable)

    def assert_database_valid(self):
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_explicit_backend_selection_uses_native_implementation(self):
        self.assertIsNone(self.python._NATIVE)
        self.assertIs(self.cpp._NATIVE, native)
        cache = self.public_cache(self.cpp, lie_executable="must-not-run")
        self.assertEqual(cache.get_decomposition("A2", (1, 0), (1,)), {(1, 0): 1})

    def test_schema_one_cross_reads_large_signed_and_zero_decompositions(self):
        huge = 10 ** 130 + 37
        entries = [
            (("A1", (1,), (2, 0)), {(0,): -huge, (2,): huge + 3}),
            (("A1", (2,), (2, 0)), {}),
        ]
        python = self.public_cache(self.python, lie_executable="must-not-run")
        python._write_decompositions(entries)
        python.close()
        cpp = self.cache(lie_executable="must-not-run")
        for request, expected in entries:
            self.assertEqual(cpp.get_decomposition(*request), expected)
        native_entries = [
            (("A1", (3,), (2, 0)), {(0,): huge, (4,): -huge}),
            (("A1", (4,), (2, 0)), {}),
        ]
        cpp.write_decompositions(native_entries)
        cpp.close()
        for request, expected in native_entries:
            self.assertEqual(python.get_decomposition(*request), expected)
        with sqlite3.connect(self.path) as connection:
            payloads = connection.execute("SELECT terms_json FROM character_decompositions")
            self.assertTrue(all(isinstance(coefficient, str)
                                for (payload,) in payloads
                                for _, coefficient in json.loads(payload)))
        self.assert_database_valid()

    def test_native_coefficients_exceed_python_decimal_conversion_limit(self):
        huge = 10 ** 6000 + 123
        expected = {(0,): huge, (2,): -huge}
        payload = native.encode_decomposition(expected)
        self.assertEqual(native.decode_decomposition(payload), expected)
        self.assertEqual(native.parse_lie_decomposition(
            native.format_lie_decomposition(expected), 1), expected)
        cpp = self.cache(lie_executable="must-not-run")
        cpp.write_decompositions([(("A1", (1,), (2, 0)), expected)])
        cpp.write_singlets("A1", {"large-integer-test": -huge})
        cpp.close()
        self.assertEqual(cpp.get_decomposition("A1", (1,), (2,)), expected)
        self.assertEqual(cpp.lookup_singlets("A1", ["large-integer-test"]),
                         {"large-integer-test": -huge})
        with sqlite3.connect(self.path) as connection:
            decimal = connection.execute("SELECT coefficient FROM singlet_coefficients").fetchone()[0]
            self.assertEqual(len(decimal), 6002)
            self.assertTrue(decimal.startswith("-1"))
            self.assertTrue(decimal.endswith("123"))

    def test_schema_one_singlet_keys_interoperate_in_both_directions(self):
        huge = -(10 ** 121 + 19)
        products = [[], [{}], [{(0,): huge}], [{(1,): huge}, {(1,): 1}]]
        expected = [1, 0, huge, huge]
        python = self.public_cache(self.python, lie_executable="must-not-run")
        self.assertEqual(python.singlet_multiplicities("A1", 1, products), expected)
        python.close()
        cpp = self.cache(lie_executable="must-not-run")
        self.assertEqual(cpp.singlet_multiplicities("A1", 1, products), expected)
        more = [[{(0,): huge + 1}], [{(2,): -3}, {(2,): huge}]]
        self.assertEqual(cpp.singlet_multiplicities("A1", 1, more), [huge + 1, -3 * huge])
        cpp.close()
        with patch.object(python, "_compute_singlet_multiplicities",
                          side_effect=AssertionError("recomputed native singlet hit")):
            self.assertEqual(python.singlet_multiplicities("A1", 1, more),
                             [huge + 1, -3 * huge])
        self.assert_database_valid()

    def test_native_character_keys_preserve_conjugates_and_hit_before_decomposition(self):
        f, anti = ((1, 0), (1,)), ((0, 1), (1,))
        products = [[f, anti], [anti, f], [f, f], [((1, 0), (2,))], [], [anti]]
        expected = [1, 1, 0, 0, 1, 0]
        python = self.public_cache(self.python, lie_executable="must-not-run")
        self.assertEqual(python.get_singlet_multiplicities("A2", 2, products), expected)
        python.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM character_decompositions")
        cpp = self.cache(lie_executable="must-not-run")
        self.assertEqual(cpp.get_singlet_multiplicities("A2", 2, products), expected)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM character_decompositions").fetchone()[0], 0)
        self.assertEqual(cpp.get_singlet_multiplicities("A2", 2, [[anti, anti]]), [0])
        self.assertEqual(cpp.get_singlet_multiplicities("C2", 2, [[f, f]]), [1])
        cpp.close()
        with patch.object(python, "_character_singlets",
                          side_effect=AssertionError("recomputed native singlet hit")):
            self.assertEqual(python.get_singlet_multiplicities("A2", 2, [[anti, anti]]), [0])
            self.assertEqual(python.get_singlet_multiplicities("C2", 2, [[f, f]]), [1])

    def test_native_parser_preserves_arbitrary_integers_and_cancellation(self):
        huge = 10 ** 137 + 7
        text = f"+{huge} X[2,0] +-3X[0,1] +3X[0,1] -{huge + 1}X[0,0]"
        expected = {(2, 0): huge, (0, 0): -(huge + 1)}
        self.assertEqual(native.parse_lie_decomposition(text, 2), expected)
        self.assertEqual(native.parse_lie_decomposition("0", 2), {})
        self.assertEqual(native.parse_lie_decomposition(
            native.format_lie_decomposition(expected), 2), expected)
        self.assertEqual(native.decode_decomposition(native.encode_decomposition(expected)), expected)
        for malformed in ("", "error", "1X[1] garbage", "1X[1]+", "1X[-1]", "1X[1,0]"):
            with self.subTest(text=malformed), self.assertRaises((RuntimeError, ValueError)):
                native.parse_lie_decomposition(malformed, 1)

    def test_native_rejects_malformed_stored_coefficients(self):
        for coefficient in ("1 2", "- 2", "12\x00ignored", "0xff", "1.0", "", "+", "--1"):
            with self.subTest(coefficient=coefficient), self.assertRaises((ValueError, RuntimeError)):
                native.decode_decomposition(json.dumps([[[1], coefficient]]))

    def test_native_rejects_embedded_nul_before_opening_files_or_processes(self):
        with self.assertRaises(ValueError):
            cache = native.CharacterDecompositionCache(str(self.path) + "\x00ignored")
            self.addCleanup(cache.close)
            cache.get_decomposition("A1", (1,), (1,))
        self.assertFalse(self.path.exists())
        marker = self.directory / "executed-marker"
        executable = self.fake_lie(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
        )
        with self.assertRaises(ValueError):
            cache = native.CharacterDecompositionCache(str(self.path), executable + "\x00ignored")
            self.addCleanup(cache.close)
            cache.execute_lie([], False)
        self.assertFalse(marker.exists())

    def test_native_dependency_helper_rejects_negative_powers(self):
        for powers in ((-1,), (2, -1), (-(2 ** 63), -1)):
            with self.subTest(powers=powers), self.assertRaises((ValueError, OverflowError)):
                native.decomposition_dependencies(powers)

    def test_native_canonicalization_keeps_conjugates_and_rejects_inexact_keys(self):
        f, anti = ((1, 0), (1,)), ((0, 1), (1,))
        self.assertEqual(native.canonical_product("A2", [f, anti, f]),
                         (((1, 0), (2, 0)), ((0, 1), (1,))))
        self.assertEqual(native.canonical_request(" a_2 ", iter([1, 0]), iter([0, 1, 0])),
                         ("A2", (1, 0), (0, 1)))
        for labels, powers in [((1.5,), (1,)), ((True,), (1,)), ((1,), (True,)),
                               ((1,), (1.5,)), ((-1,), (1,)), ((1,), (0,))]:
            with self.subTest(labels=labels, powers=powers), self.assertRaises((TypeError, ValueError)):
                native.canonical_request("A1", labels, powers)

    def test_duality_matches_project_convention_for_complex_families(self):
        for algebra in ("A1", "A2", "A4", "B3", "C2", "D4", "D5", "D7", "E6", "E7", "G2"):
            with self.subTest(algebra=algebra):
                expected = self.python._dual_permutation(algebra)
                self.assertEqual(tuple(native.dual_permutation(algebra)), expected)
                rank = int(algebra[1:])
                left = {tuple(int(i == j) for i in range(rank)): (j + 1) * (10 ** 40 + 3)
                        for j in range(rank)}
                right = {tuple(int(i == j) for i in range(rank)): -(j + 2) for j in range(rank)}
                self.assertEqual(native.singlet_pair(algebra, left, right),
                                 self.python._singlet_pair(algebra, left, right))

    def test_invalid_schema_is_rejected_without_modifying_rows(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA user_version=999")
        cpp = self.cache(lie_executable="must-not-run")
        with self.assertRaisesRegex(RuntimeError, "schema version"):
            cpp.get_decomposition("A1", (1,), (1,))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)

    def test_native_process_failure_cannot_create_false_zero(self):
        for mode in ("missing", "stderr", "malformed", "timeout"):
            with self.subTest(mode=mode):
                self.path = self.directory / (mode + ".db")
                if mode == "missing":
                    executable = str(self.directory / "does-not-exist")
                else:
                    scripts = {
                        "stderr": "import sys\nsys.stdin.read()\nprint('0')\nsys.stderr.write('deliberate failure')\n",
                        "malformed": "import sys\nsys.stdin.read()\nprint('1X[2] followed-by-garbage')\n",
                        "timeout": "import time\ntime.sleep(3)\n",
                    }
                    executable = self.fake_lie(scripts[mode])
                cpp = self.cache(lie_executable=executable, timeout=0.15 if mode == "timeout" else 10)
                with self.assertRaises(RuntimeError):
                    cpp.get_singlet_multiplicities("A1", 1, [[((1,), (0, 1))]])
                cpp.close()
                with sqlite3.connect(self.path) as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM singlet_coefficients").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT count(*) FROM character_decompositions").fetchone()[0], 0)

    def test_native_process_retries_memory_configuration_and_filters_notices(self):
        calls = self.directory / "calls.jsonl"
        executable = self.fake_lie(
            "import json, sys\n"
            "code = sys.stdin.read()\n"
            f"with open({str(calls)!r}, 'a') as handle:\n"
            "    handle.write(json.dumps(code) + '\\n')\n"
            "if not code.startswith('maxnodes '):\n"
            "    sys.stderr.write('requires larger memory')\n"
            "else:\n"
            "    print('New tree space with maximum number of nodes: 99')\n"
            "    print('-1X[0]+1X[2]')\n"
        )
        cpp = self.cache(lie_executable=executable)
        self.assertEqual(cpp.get_decomposition("A1", (1,), (0, 1)), {(0,): -1, (2,): 1})
        programs = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(len(programs), 2)
        self.assertEqual(programs[0], "Adams(2,[1],A1)")
        self.assertTrue(programs[1].startswith("maxnodes 9999999\nmaxobjects 9999999\n"))

    def test_native_process_drains_both_output_pipes_while_sending_large_input(self):
        executable = self.fake_lie(
            "import sys\n"
            "sys.stdout.write('o' * 262144)\n"
            "sys.stdout.flush()\n"
            "sys.stderr.write('e' * 262144)\n"
            "sys.stderr.flush()\n"
            "code = sys.stdin.read()\n"
            "sys.stdout.write('\\n' + str(len(code)))\n"
        )
        cpp = self.cache(lie_executable=executable, timeout=5)
        result = cpp.execute_lie(["x" * 262144], False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout_text, "o" * 262144 + "\n262144")
        self.assertEqual(result.stderr_text, "e" * 262144)
        cpp.close()
        self.fake_lie("import sys\nsys.exit(7)\n")
        result = cpp.execute_lie(["x" * 262144], False)
        self.assertEqual(result.returncode, 7)

    def test_native_lie_text_output_matches_python_newline_semantics(self):
        mixed = "1X[0]\r2X[1]\r\n3X[2]\n"
        self.assertEqual(native.lie_output_lines(mixed), ["1X[0]", "2X[1]", "3X[2]"])
        self.assertEqual(native.lie_output_lines(mixed), self.python.CharacterDecompositionCache._lie_output_lines(mixed))
        executable = self.fake_lie(
            "import sys\n"
            "sys.stdin.read()\n"
            f"sys.stdout.buffer.write({mixed.encode()!r})\n"
            "sys.stderr.buffer.write(b'first\\rsecond\\r\\n')\n"
        )
        python = self.public_cache(self.python, lie_executable=executable)
        expected = python._execute_lie(["ignored"], configure_memory=False)
        actual = self.cache(lie_executable=executable).execute_lie(["ignored"], False)
        self.assertEqual(actual.returncode, expected.returncode)
        self.assertEqual(actual.stdout_text, expected.stdout)
        self.assertEqual(actual.stderr_text, expected.stderr)

    def test_public_native_backend_accepts_pathlike_lie_executable(self):
        executable = self.fake_lie("import sys\nsys.stdin.read()\nprint('-1X[0]+1X[2]')\n")
        for backend in (self.python, self.cpp):
            with self.subTest(backend=backend.__name__):
                self.path = self.directory / (backend.__name__ + ".db")
                cpp = self.public_cache(backend, lie_executable=Path(executable))
                self.assertEqual(cpp.get_decomposition("A1", (1,), (0, 1)),
                                 {(0,): -1, (2,): 1})

    def test_independent_native_thread_connections_write_shared_file(self):
        def calculate(label):
            cache = native.CharacterDecompositionCache(str(self.path), lie_executable="must-not-run")
            try:
                return cache.get_decomposition("A1", (label,), (1,))
            finally:
                cache.close()
        labels = list(range(1, 9)) * 3
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(calculate, labels)), [{(n,): 1} for n in labels])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM character_decompositions").fetchone()[0], 8)
        self.assert_database_valid()

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires POSIX fork")
    def test_native_client_reopens_connection_after_fork(self):
        cpp = self.cache(lie_executable="must-not-run")
        self.assertEqual(cpp.get_decomposition("A1", (1,), (1,)), {(1,): 1})
        context = multiprocessing.get_context("fork")
        reader, writer = context.Pipe(duplex=False)
        child = context.Process(target=fork_cache_read, args=(cpp, writer))
        child.start()
        writer.close()
        try:
            self.assertTrue(reader.poll(10), "forked cache did not return")
            self.assertEqual(reader.recv(), ("ok", {(2,): 1}))
            child.join(10)
            self.assertEqual(child.exitcode, 0)
        finally:
            reader.close()
            if child.is_alive():
                child.terminate()
                child.join(10)
        self.assertEqual(cpp.get_decomposition("A1", (2,), (1,)), {(2,): 1})
        self.assert_database_valid()

    @unittest.skipUnless(shutil.which("lie"), "LiE is required")
    def test_full_native_cache_matches_independent_su2_weight_expansion(self):
        powers = [(0, 1), (3,), (0, 3), (1, 1, 1), (2, 0, 0, 1), (0, 2, 2)]
        cpp = self.cache()
        expected = [su2_weights(p) for p in powers]
        requests = [("A1", (1,), p) for p in powers]
        self.assertEqual(cpp.get_decompositions(requests), expected)
        self.assertEqual(cpp.get_singlet_multiplicities(
            "A1", 1, [[((1,), p)] for p in powers]), [result.get((0,), 0) for result in expected])
        cpp.close()
        python = self.public_cache(self.python, lie_executable="must-not-run")
        self.assertEqual(python.get_decompositions(requests), expected)

    @unittest.skipUnless(shutil.which("lie"), "LiE is required")
    def test_full_native_cache_reuses_persisted_composite_blocks(self):
        python = self.public_cache(self.python)
        for powers in ((0, 2), (0, 0, 2)):
            python.get_decomposition("A1", (1,), powers)
        python.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM character_decompositions WHERE adams_order NOT IN (4,6)")
        cpp = self.cache()
        self.assertEqual(cpp.get_decomposition("A1", (1,), (0, 2, 2)), su2_weights((0, 2, 2)))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM character_decompositions").fetchone()[0], 3)

    @unittest.skipUnless(shutil.which("lie"), "LiE is required")
    def test_full_native_a2_mixed_products_match_python_with_independent_databases(self):
        requests = [("A2", (1, 0), powers) for powers in ((0, 1), (3,), (1, 1), (0, 2), (1, 0, 1))]
        requests += [("A2", (0, 1), (1, 1)), ("A2", (1, 1), (0, 1))]
        f, anti, adj = ((1, 0), (1,)), ((0, 1), (1,)), ((1, 1), (1,))
        characters = [[f, f, f], [f, f, anti, anti], [f, anti, adj],
                      [((1, 0), (0, 1)), anti, anti],
                      [((1, 0), (1, 1)), ((0, 1), (2,)), adj]]
        huge = 10 ** 50 + 27
        decomposed = [[{(1, 0): 1}, {(1, 0): 1}, {(1, 0): -huge}],
                      [{(1, 0): -2, (0, 1): 3}, {(0, 1): 4}, {(1, 1): -5, (0, 0): 2}],
                      [{(1, 0): 1}, {(0, 1): 1}, {(1, 1): 1}, {(0, 0): -7}],
                      [{(1, 0): 1}, {}, {(1, 0): 1}]]
        python = self.public_cache(self.python)
        expected_decompositions = python.get_decompositions(requests)
        expected_characters = python.get_singlet_multiplicities("A2", 2, characters)
        expected_decomposed = python.singlet_multiplicities("A2", 2, decomposed)
        self.assertEqual(expected_characters[:3], [1, 2, 1])
        self.assertEqual(expected_decomposed[0], -huge)
        python.close()
        self.path = self.directory / "native-independent.db"
        cpp = self.cache()
        self.assertEqual(cpp.get_decompositions(requests), expected_decompositions)
        self.assertEqual(cpp.get_singlet_multiplicities("A2", 2, characters), expected_characters)
        self.assertEqual(cpp.singlet_multiplicities("A2", 2, decomposed), expected_decomposed)
        self.assert_database_valid()


if __name__ == "__main__":
    unittest.main(verbosity=2)
