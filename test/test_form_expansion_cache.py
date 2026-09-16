from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from fractions import Fraction
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.form_utils import run_form
from index.form_expansion_cache import (
    DEFAULT_FORM_CACHE_DATABASE,
    FormExpansionCache,
    IndexFormTerm,
)
from index.n2_theory_index import calculate_index


def constant_term(value):
    return IndexFormTerm(Fraction(value), 0, 0, 0, ())


def form_program(value):
    return f"Local result = {value};\n.sort\nPrint result;\n.end\n"


def populate_expansion_in_process(request):
    path, value = request
    with FormExpansionCache(database_path=path) as cache:
        return cache.get_expansion(form_program(value))


def calculate_index_in_process(request):
    directory, form_executable = request
    result = calculate_index(
        {"algebra": "A2", "hypermultiplets": [
            {"representation": "fundamental", "number": 6, "kind": "full"}
        ]},
        6,
        char_cache_database_path=Path(directory) / "characters.db",
        form_executable=form_executable,
        processes=1,
    )
    return {tuple(map(int, powers)): int(coefficient)
            for powers, coefficient in result.dict().items()}


class ExpansionSerializationTests(unittest.TestCase):
    def test_round_trip_preserves_exact_coefficients_order_and_nested_tuples(self):
        terms = [
            IndexFormTerm(Fraction(-(10 ** 120 + 1), 7), 18, -5, 12,
                          ((0, (2, 0, 1)), (7, (0, 3, 0)))),
            constant_term(1),
            IndexFormTerm(Fraction(0), 4, 3, -6, ((2, ()),)),
        ]
        payload = FormExpansionCache._encode_expansion(terms)
        decoded = FormExpansionCache._decode_expansion(payload)
        self.assertEqual(decoded, terms)
        self.assertIsInstance(decoded, list)
        self.assertIsInstance(decoded[0].coefficient, Fraction)
        self.assertIsInstance(decoded[0].characters, tuple)
        self.assertIsInstance(decoded[0].characters[0][1], tuple)
        self.assertIsInstance(json.loads(payload)[0][0], str)
        self.assertEqual(FormExpansionCache._encode_expansion(decoded), payload)

    def test_empty_expansion_round_trip(self):
        self.assertEqual(
            FormExpansionCache._decode_expansion(
                FormExpansionCache._encode_expansion([])
            ),
            [],
        )

    def test_parser_preserves_signed_rationals_and_adams_multiplicities(self):
        output = "result = 1 - d(7,5)*t^8*y^-2*u^3*C0(1)^2*C2(2)*C2(2);"
        expected = [
            constant_term(1),
            IndexFormTerm(Fraction(-7, 5), 8, -2, 3,
                          ((0, (2, 0)), (2, (0, 2, 0, 0)))),
        ]
        self.assertEqual(FormExpansionCache.parse_form_output(output), expected)


class FormExpansionCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "nested" / "form.db"

    def cache(self, **kwargs):
        cache = FormExpansionCache(database_path=self.path, **kwargs)
        self.addCleanup(cache.close)
        return cache

    def test_paths_are_resolved_and_database_is_created_lazily(self):
        self.assertEqual(FormExpansionCache().database_path, DEFAULT_FORM_CACHE_DATABASE)
        cache = self.cache()
        self.assertEqual(cache.database_path, self.path.resolve())
        self.assertFalse(self.path.exists())
        with patch("index.form_expansion_cache.run_form", return_value="result = 1;"):
            self.assertEqual(cache.get_expansion("program"), [constant_term(1)])
        self.assertTrue(self.path.is_file())

    def test_miss_runs_form_and_stores_the_verbatim_program_as_primary_key(self):
        program = "* quoted 'text' and Unicode: 전개\nLocal result = 1;\n.end\n"
        cache = self.cache(form_executable="custom-form", timeout=17)
        with patch("index.form_expansion_cache.run_form", return_value="result = 1;") as run:
            self.assertEqual(cache.get_expansion(program), [constant_term(1)])
            run.assert_called_once_with(program, form_executable="custom-form", timeout=17,
                                        tform_executable="tform", form_threads=1)
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT program, expansion_json FROM form_expansions"
            ).fetchone()
            self.assertEqual(row[0], program)
            self.assertEqual(FormExpansionCache._decode_expansion(row[1]), [constant_term(1)])
            columns = connection.execute("PRAGMA table_info(form_expansions)").fetchall()
            self.assertEqual([column[1] for column in columns if column[5]], ["program"])
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_hit_after_reopening_needs_neither_form_nor_parser(self):
        cache = self.cache()
        with patch("index.form_expansion_cache.run_form", return_value="result = 1;"):
            cache.get_expansion("program").clear()
        cache.close()
        with self.cache(form_executable="missing-form") as reopened:
            with (
                patch("index.form_expansion_cache.run_form", side_effect=AssertionError("FORM on hit")),
                patch.object(reopened, "parse_form_output", side_effect=AssertionError("parse on hit")),
            ):
                self.assertEqual(reopened.get_expansion("program"), [constant_term(1)])
                reopened.get_expansion("program").append(constant_term(9))
                self.assertEqual(reopened.get_expansion("program"), [constant_term(1)])

    def test_case_whitespace_and_newlines_are_part_of_the_key(self):
        programs = ["Program", "program", "program ", "program\n", "program\r\n"]
        cache = self.cache()
        with patch("index.form_expansion_cache.run_form",
                   side_effect=[f"result = {i};" for i in range(len(programs))]) as run:
            for i, program in enumerate(programs):
                self.assertEqual(cache.get_expansion(program), [constant_term(i)])
            for i, program in enumerate(programs):
                self.assertEqual(cache.get_expansion(program), [constant_term(i)])
            self.assertEqual(run.call_count, len(programs))
        with sqlite3.connect(self.path) as connection:
            stored = connection.execute("SELECT program FROM form_expansions").fetchall()
            self.assertEqual({row[0] for row in stored}, set(programs))

    def test_empty_expansion_is_a_cache_hit(self):
        cache = self.cache()
        with (
            patch("index.form_expansion_cache.run_form", return_value="result = 0;") as run,
            patch.object(cache, "parse_form_output", return_value=[]),
        ):
            self.assertEqual(cache.get_expansion("zero"), [])
            self.assertEqual(cache.get_expansion("zero"), [])
            run.assert_called_once()

    def test_execution_and_parse_failures_do_not_poison_cache(self):
        cache = self.cache()
        for failure in (RuntimeError("FORM failed"), "unparseable output", "result = unknown;"):
            with self.subTest(failure=failure):
                options = ({"side_effect": failure} if isinstance(failure, Exception)
                           else {"return_value": failure})
                with patch("index.form_expansion_cache.run_form", **options):
                    with self.assertRaises(RuntimeError):
                        cache.get_expansion("program")
                with sqlite3.connect(self.path) as connection:
                    self.assertEqual(connection.execute(
                        "SELECT count(*) FROM form_expansions"
                    ).fetchone()[0], 0)
        with patch("index.form_expansion_cache.run_form", return_value="result = 1;"):
            self.assertEqual(cache.get_expansion("program"), [constant_term(1)])

    def test_form_execution_does_not_hold_a_write_transaction(self):
        cache = self.cache()

        def run_without_lock(program, **kwargs):
            self.assertFalse(cache._connection().in_transaction)
            with sqlite3.connect(self.path, timeout=0.1) as other:
                other.execute("BEGIN IMMEDIATE")
                other.execute(
                    "INSERT INTO form_expansions VALUES (?, ?)",
                    ("other", FormExpansionCache._encode_expansion([constant_term(2)])),
                )
            return "result = 1;"

        with patch("index.form_expansion_cache.run_form", side_effect=run_without_lock):
            self.assertEqual(cache.get_expansion("program"), [constant_term(1)])
        self.assertEqual(cache.get_expansion("other"), [constant_term(2)])

    def test_close_releases_connection_and_allows_reuse(self):
        cache = self.cache()
        with patch("index.form_expansion_cache.run_form", return_value="result = 1;"):
            with cache:
                cache.get_expansion("program")
                connection = cache._connection()
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        self.assertEqual(cache.get_expansion("program"), [constant_term(1)])

    def test_rejects_non_text_program_before_creating_database(self):
        with self.assertRaises(TypeError):
            self.cache().get_expansion(b"program")
        self.assertFalse(self.path.exists())

    def test_threads_share_one_client_and_preserve_all_entries(self):
        cache = self.cache()
        values = [1, 2, 3, 4] * 3

        def calculate(value):
            try:
                return cache.get_expansion(str(value))
            finally:
                cache.close()

        with patch("index.form_expansion_cache.run_form",
                   side_effect=lambda program, **kwargs: f"result = {program};"):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(calculate, values))
        self.assertEqual(results, [[constant_term(value)] for value in values])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM form_expansions"
            ).fetchone()[0], 4)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "fork is required")
    def test_child_process_reopens_an_inherited_connection(self):
        cache = self.cache()
        with patch("index.form_expansion_cache.run_form", return_value="result = 1;"):
            cache.get_expansion("program")
        parent_connection = cache._connection()
        context = multiprocessing.get_context("fork")
        receiver, sender = context.Pipe(duplex=False)

        def read_in_child():
            try:
                terms = cache.get_expansion("program")
                sender.send((terms, cache._local.pid == os.getpid(),
                             cache._connection() is not parent_connection))
            finally:
                cache.close()
                sender.close()

        process = context.Process(target=read_in_child)
        process.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(10), "child did not finish its cache lookup")
            self.assertEqual(receiver.recv(), ([constant_term(1)], True, True))
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
            receiver.close()
            process.close()
        self.assertIs(cache._connection(), parent_connection)
        self.assertEqual(cache.get_expansion("program"), [constant_term(1)])

    def test_simultaneous_misses_for_same_program_store_one_complete_row(self):
        cache = self.cache()
        barrier = threading.Barrier(4)

        def run_together(program, **kwargs):
            barrier.wait(timeout=10)
            return "result = d(-7,3);"

        def calculate(_):
            try:
                return cache.get_expansion("same program")
            finally:
                cache.close()

        with patch("index.form_expansion_cache.run_form", side_effect=run_together) as run:
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(calculate, range(4)))
        self.assertEqual(run.call_count, 4)
        self.assertEqual(results, [[constant_term(Fraction(-7, 3))]] * 4)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM form_expansions"
            ).fetchone()[0], 1)

    @unittest.skipUnless(shutil.which("form"), "FORM is required")
    def test_real_form_output_round_trips_through_sqlite(self):
        program = """Symbols t,y,u;
CFunctions d,C0,C1;
PolyRatFun d;
Local result = 1 - d(7,5)*t^4*y^-2*u^3*C0(1)^2*C1(2);
.sort
Print result;
.end
"""
        expected = [constant_term(1), IndexFormTerm(
            Fraction(-7, 5), 4, -2, 3, ((0, (2, 0)), (1, (0, 1)))
        )]
        with self.cache() as cache:
            self.assertCountEqual(cache.get_expansion(program), expected)
        with self.cache(form_executable="missing-form") as cache:
            self.assertCountEqual(cache.get_expansion(program), expected)

    @unittest.skipUnless(shutil.which("form"), "FORM is required")
    def test_independent_processes_write_overlapping_entries(self):
        values = [1, 2, 3, 4, 3, 2, 1, 4]
        with ProcessPoolExecutor(max_workers=3,
                                 mp_context=multiprocessing.get_context("spawn")) as executor:
            results = list(executor.map(
                populate_expansion_in_process, [(self.path, value) for value in values]
            ))
        self.assertEqual(results, [[constant_term(value)] for value in values])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM form_expansions"
            ).fetchone()[0], 4)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")


@unittest.skipUnless(shutil.which("form") and shutil.which("lie"), "FORM and LiE are required")
class IndexExpansionCacheIntegrationTests(unittest.TestCase):
    def test_index_reuses_expansion_and_honors_explicit_cache_path(self):
        data = {"algebra": "A2", "hypermultiplets": [
            {"representation": "fundamental", "number": 6, "kind": "full"}
        ]}
        with tempfile.TemporaryDirectory() as directory:
            form_path = Path(directory) / "expansions" / "custom.db"
            options = dict(char_cache_database_path=Path(directory) / "characters" / "custom.db",
                           form_cache_database_path=form_path, processes=1)
            with patch("index.form_expansion_cache.run_form", wraps=run_form) as run:
                cold = calculate_index(data, 6, **options)
                warm = calculate_index(data, 6, form_executable="missing-form", **options)
                run.assert_called_once()
            t, y, u = cold.parent().gens()
            expected = (1 + (36 / u**2 + u**4) * t**4
                        - u**2 * (y + 1/y) * t**5
                        + (40 / u**3 - 36 + u**6) * t**6)
            self.assertEqual(cold, expected)
            self.assertEqual(warm, expected)
            self.assertTrue(form_path.exists())
            self.assertTrue(options["char_cache_database_path"].is_file())
            self.assertFalse((Path(options["char_cache_database_path"]).parent / DEFAULT_FORM_CACHE_DATABASE.name).exists())

    def test_form_cache_defaults_beside_explicit_character_database(self):
        with tempfile.TemporaryDirectory() as directory:
            character_path = Path(directory) / "custom" / "characters.db"
            calculate_index({"algebra": "A1", "hypermultiplets": []}, 4,
                            char_cache_database_path=character_path, processes=1)
            self.assertTrue(character_path.is_file())
            self.assertTrue(character_path.with_name(DEFAULT_FORM_CACHE_DATABASE.name).exists())

    def test_index_calculations_in_separate_processes_share_both_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            context = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=3, mp_context=context) as executor:
                cold = list(executor.map(calculate_index_in_process, [(directory, "form")] * 3))
                warm = list(executor.map(calculate_index_in_process, [(directory, "missing-form")] * 3))
            expected = {
                (0, 0, 0): 1, (4, 0, -2): 36, (4, 0, 4): 1,
                (5, 1, 2): -1, (5, -1, 2): -1,
                (6, 0, -3): 40, (6, 0, 0): -36, (6, 0, 6): 1,
            }
            self.assertEqual(cold, [expected] * 3)
            self.assertEqual(warm, cold)
            with sqlite3.connect(Path(directory) / DEFAULT_FORM_CACHE_DATABASE.name) as connection:
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM form_expansions"
                ).fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
