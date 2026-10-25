"""Standalone C++ singlet projection against Python and independent weights.

All caches and executables live in temporary directories. No production Python
binding, MySQL database, or project character-cache file is used.
"""
from fractions import Fraction
import json
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.form_utils import run_form
from index.form_expansion_cache import FormExpansionCache, IndexFormTerm
from index import n2_theory_index as idx
from test.test_cpp_char_decomposition_cache import load_backend
from test.test_singlet_projection import su3_weight_singlet
from test.test_character_decomposition_cache import su2_fundamental_adams_product

ROOT = Path(__file__).resolve().parents[1]


def term(coefficient=1, powers=(0, 0, 0), characters=()):
    coefficient = Fraction(coefficient)
    return {"coefficient": [str(coefficient.numerator), str(coefficient.denominator)],
            "powers": list(powers), "characters": list(characters)}


def python_terms(terms):
    return [IndexFormTerm(Fraction(*map(int, item["coefficient"])), *item["powers"],
                          tuple((index, tuple(powers)) for index, powers in item["characters"]))
            for item in terms]


class CppIndexSingletProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = load_backend("python")
        compiler = shlex.split(os.environ.get("CXX", "/usr/bin/g++"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise unittest.SkipTest("a C++17 compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="cpp-index-project-build-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.executable = Path(cls.temporary.name) / "index_singlet_project_driver"
        json_include = Path(os.environ.get("NLOHMANN_JSON_INCLUDE_DIR",
            ROOT / "index/cpp/_build/deps/nlohmann_json/include"))
        if not (json_include / "nlohmann/json.hpp").is_file():
            raise RuntimeError("nlohmann/json headers missing; run index/cpp/build_index_calculation.py --install-deps")
        result = subprocess.run(["make", "-C", str(ROOT / "common/cpp/LiE"),
                                 "-f", "Makefile.shared", "-j4"],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        command = [*compiler, "-std=c++17", "-O2", "-pthread", "-Wall", "-Wextra", "-Wpedantic",
                   *shlex.split(os.environ.get("CPP_INDEX_CXXFLAGS", "")),
                   "-I", str(ROOT), "-I", str(json_include)]
        include = os.environ.get("GMP_INCLUDE_DIR")
        library = os.environ.get("GMP_LIBRARY_DIR")
        if include is None and (Path(sys.prefix) / "include/gmpxx.h").is_file():
            include = str(Path(sys.prefix) / "include")
            library = library or str(Path(sys.prefix) / "lib")
        if include:
            command += ["-I", include]
        command += [str(ROOT / path) for path in (
            "test/cpp/index_singlet_project_driver.cpp", "index/cpp/index_expand.cpp",
            "index/cpp/index_singlet_project.cpp", "index/cpp/char_decomposition_cache.cpp",
            "common/cpp/lie.cpp")]
        if library:
            command += ["-L", library, f"-Wl,-rpath,{library}"]
        lie = ROOT / "common/cpp/LiE"
        command += ["-L", str(lie), f"-Wl,-rpath,{lie}", "-llie", "-ldl", "-lgmpxx", "-lgmp",
                    "-lsqlite3", "-o", str(cls.executable)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(f"C++ projection build failed:\n{result.stdout}\n{result.stderr}")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cpp-index-project-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "characters.db"

    def invoke(self, data, mode="convenience", success=True):
        environment = dict(os.environ, PATH="") # Native misses must not launch lie.
        result = subprocess.run([str(self.executable), str(self.path), mode],
                                input=json.dumps(data), text=True, capture_output=True,
                                cwd=self.directory, env=environment, timeout=120)
        if not success:
            self.assertNotEqual(result.returncode, 0)
            return result.stderr
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        keys = [tuple(row[:3]) for row in rows]
        self.assertEqual(keys, sorted(set(keys)))
        projected = {}
        for t, y, u, numerator, denominator in rows:
            coefficient = Fraction(int(numerator), int(denominator))
            self.assertNotEqual(coefficient, 0)
            self.assertEqual((str(coefficient.numerator), str(coefficient.denominator)),
                             (numerator, denominator))
            projected[t, y, u] = coefficient
        return projected

    def reference(self, terms, factors, characters, path=None):
        metadata = tuple(SimpleNamespace(algebra=SimpleNamespace(cartan_type=algebra,
                           rank=int(algebra[1:]))) for algebra in factors)
        with self.python.CharacterDecompositionCache(database_path=path or self.directory / "python.db",
                                                      max_workers=1, lie_backend="subprocess") as cache:
            return idx._project_terms(terms, metadata,
                                      tuple((position, tuple(labels)) for position, labels in characters), cache)

    def test_empty_vacuum_and_free_terms_need_no_cache(self):
        data = {"factors": [], "characters": [], "terms": []}
        self.assertEqual(self.invoke(data), {})
        data["terms"] = [term(1), term(Fraction(2, 3), (8, -3, 5)),
                         term(Fraction(-2, 3), (8, -3, 5))]
        self.assertEqual(self.invoke(data), {(0, 0, 0): 1})
        self.assertFalse(self.path.exists())

    def test_default_cache_and_native_lie_without_executable(self):
        data = {"factors": ["A1"], "characters": [[0, [1]]],
                "terms": [term(3, (4, -1, 2), [(0, [0, 1])])]}
        self.assertEqual(self.invoke(data, "default"), {(4, -1, 2): -3})
        self.assertTrue((self.directory / "char_decomposition_cache.db").exists())

    def test_complex_representations_and_independent_factors(self):
        data = {"factors": ["A2", "A2", "A1"],
                "characters": [[0, [1, 0]], [0, [0, 1]], [1, [1, 0]], [1, [0, 1]], [2, [1]]],
                "terms": [term(1, (2, 0, 0), [(0, [1]), (2, [1])]), # no cross-factor pairing
                          term(2, (4, 0, 0), [(0, [1]), (1, [1])]),
                          term(3, (6, 0, 0), [(0, [1]), (1, [1]), (2, [2])]),
                          term(5, (8, 0, 0), [(4, [2]), (3, [1]), (2, [1])]),
                          term(7, (10, 0, 0), [(0, [3]), (2, [3])])]}
        self.assertEqual(self.invoke(data, "borrowed"),
                         {(4, 0, 0): 2, (8, 0, 0): 5, (10, 0, 0): 7})

    def test_adams_products_match_independent_su2_and_su3_weights(self):
        rng = random.Random(15102026)
        specs = [[0, [1]], [1, [1, 0]], [1, [0, 1]], [1, [1, 1]]]
        terms, expected = [], {}
        for i in range(40):
            su2 = tuple(rng.randrange(3) for _ in range(3))
            parts = [(0, list(su2))] if any(su2) else []
            product = []
            for _ in range(rng.randrange(4)):
                character = rng.randrange(1, 4)
                powers = [0] * rng.randrange(1, 4)
                powers[-1] = 1
                parts.append((character, powers))
                product.append((tuple(specs[character][1]), tuple(powers)))
            coefficient = Fraction((-1) ** i * (i + 1), i + 2)
            value = (coefficient * su2_fundamental_adams_product(su2).get((0,), 0)
                     * su3_weight_singlet(product))
            terms.append(term(coefficient, (i, -i, i - 4), parts))
            if value:
                expected[i, -i, i - 4] = value
        rng.shuffle(terms)
        data = {"factors": ["A1", "A2"], "characters": specs, "terms": terms}
        self.assertEqual(self.invoke(data), expected)

    def test_exact_large_rationals_aliases_padding_and_cancellation(self):
        huge = 10**140 + 19
        data = {"factors": [" a_1 "], "characters": [[0, [1]], [0, [1]], [0, [0]]],
                "terms": [term(Fraction(huge, 7), (4, -9, 2), [(1, [1, 0]), (0, [1])]),
                          term(Fraction(-huge, 7), (4, -9, 2), [(0, [2])]),
                          term(Fraction(huge, 11), (6, 2, -3), [(2, [0, 0, 1])]),
                          term(Fraction(huge, 13), (8, 2, -3), [(0, [0, 1])])]}
        expected = {(6, 2, -3): Fraction(huge, 11), (8, 2, -3): Fraction(-huge, 13)}
        self.assertEqual(self.invoke(data), expected)
        self.assertEqual(self.invoke(data, "cache-only"), expected)

    @unittest.skipUnless(shutil.which("lie"), "LiE executable is required for Python reference")
    def test_cache_interoperates_with_python_in_both_directions(self):
        data = {"factors": ["A2"], "characters": [[0, [1, 0]], [0, [0, 1]]],
                "terms": [term(2, (4, 1, -2), [(0, [1]), (1, [1])]),
                          term(3, (8, 0, 0), [(0, [3])]),
                          term(5, (10, 0, 0), [(0, [0, 0, 1])])]}
        expected = self.reference(python_terms(data["terms"]), data["factors"], data["characters"], self.path)
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM character_decompositions")
        self.assertEqual(self.invoke(data, "cache-only"), expected)
        data["terms"].append(term(7, (12, 0, 0), [(0, [2]), (1, [2])]))
        actual = self.invoke(data)
        with patch.object(self.python.CharacterDecompositionCache, "_character_singlets",
                          side_effect=AssertionError("recomputed a C++ cache hit")):
            self.assertEqual(self.reference(python_terms(data["terms"]), data["factors"],
                                            data["characters"], self.path), actual)

    def test_invalid_input_is_rejected_before_cache_writes(self):
        base = {"factors": ["A1"], "characters": [[0, [1]]], "terms": [term()]}
        invalid = [dict(base, factors=["X2"]), dict(base, factors=["B1"]),
                   dict(base, factors=["D2"]), dict(base, factors=["A9999999999999999999999"]),
                   dict(base, characters=[[-1, [1]]]), dict(base, characters=[[1, [1]]]),
                   dict(base, characters=[[0, [-1]]]), dict(base, characters=[[0, []]]),
                   dict(base, characters=[[0, [1, 0]]]),
                   dict(base, terms=[term(powers=(-1, 0, 0))]),
                   dict(base, terms=[term(characters=[(-1, [1])])]),
                   dict(base, terms=[term(characters=[(1, [1])])]),
                   dict(base, terms=[term(characters=[(0, [])])]),
                   dict(base, terms=[term(characters=[(0, [0, 0])])]),
                   dict(base, terms=[term(characters=[(0, [-1])])])]
        for data in invalid:
            with self.subTest(data=data):
                self.assertIn("invalid_argument", self.invoke(data, success=False))
                self.assertFalse(self.path.exists())
        data = dict(base, terms=[term(characters=[(0, [0, 2**63 - 1])])])
        self.assertIn("overflow_error", self.invoke(data, success=False))
        self.assertFalse(self.path.exists())

    def test_cache_miss_failure_is_not_saved_as_zero(self):
        data = {"factors": ["A1"], "characters": [[0, [1]]],
                "terms": [term(1, (4, 0, 0), [(0, [0, 1])])]}
        self.assertIn("LiE", self.invoke(data, "cache-only", success=False))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM singlet_coefficients").fetchone()[0], 0)
        self.assertEqual(self.invoke(data), {(4, 0, 0): -1})

    @unittest.skipUnless(shutil.which("lie") and shutil.which("form"), "FORM and LiE required")
    def test_complete_unordered_pipeline_matches_form_and_python_projection(self):
        cases = [
            (10, ["A1"], [[0, [2]], [0, [1]]], [0], {(1,): 8}),
            (8, ["A2"], [[0, [1, 1]], [0, [1, 0]], [0, [0, 1]]], [0], {(1,): 6, (2,): 6}),
            (8, ["C2"], [[0, [2, 0]], [0, [1, 0]]], [0], {(1,): 4}),
            (6, ["G2"], [[0, [0, 1]], [0, [1, 0]]], [0], {(1,): 2}),
            (10, ["A1", "A1"], [[0, [2]], [1, [2]], [0, [1]], [1, [1]]], [0, 1], {(2, 3): 2}),
            (8, ["A1", "A2"], [[0, [2]], [1, [1, 1]], [0, [1]], [1, [1, 0]], [1, [0, 1]]],
             [0, 1], {(2, 3): 2, (2, 4): 2, (): 1}),
            (8, [], [], [], {(): -3}),
        ]
        for case, (order, factors, specs, vectors, matter) in enumerate(cases):
            with self.subTest(case=case):
                output = run_form(idx._build_form_program(order, len(specs), tuple(vectors), matter),
                                  form_executable="form", timeout=120)
                terms = FormExpansionCache.parse_form_output(re.sub(r"\\\r?\n\s*", "", output))
                expected = self.reference(terms, factors, specs, self.directory / f"python-{case}.db")
                data = {"factors": factors, "characters": specs, "expansion": {
                    "order": order, "vectors": vectors,
                    "matter": [[list(key), str(value)] for key, value in matter.items()]}}
                for workers in (1, 4):
                    data["expansion"]["workers"] = workers
                    self.assertEqual(self.invoke(data), expected)
                data["expansion"]["sorted"] = True
                self.assertEqual(self.invoke(data), expected)


if __name__ == "__main__":
    unittest.main()
