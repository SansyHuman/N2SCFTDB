"""Production native pipeline, exact compatibility, and selection contracts."""
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from index import n2_theory_index as idx

SU2 = {"algebra": "A1", "hypermultiplets": [{"representation": "fundamental", "number": 4}]}
SU3 = {"algebra": "A2", "hypermultiplets": [{"representation": "fundamental", "number": 6}]}


class NativeCalculateIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-index-api-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def calculate(self, data=SU2, order=8, **options):
        return idx.calculate_index(data, order,
            char_cache_database_path=self.directory / "characters.db",
            form_cache_database_path=self.directory / "form.db", **options)

    def test_default_uses_native_without_form_and_keeps_return_type(self):
        with patch.object(idx, "_build_form_program", side_effect=AssertionError("FORM used")), \
             patch.object(idx, "_project_terms", side_effect=AssertionError("Python projection used")):
            value = self.calculate(form_executable="/missing/form", tform_executable="/missing/tform")
        self.assertIs(value.parent(), idx.INDEX_POLYNOMIAL_RING)
        self.assertTrue(self.directory.joinpath("characters.db").is_file())
        self.assertFalse(self.directory.joinpath("form.db").exists())
        self.assertEqual(idx.parse_index_polynomial(str(value)), value)

    @unittest.skipUnless(shutil.which("form") and shutil.which("lie"), "FORM and LiE needed")
    def test_exact_native_python_equality_including_disconnected_and_free_matter(self):
        cases = [SU2, SU3,
            {"gauge_groups": [{"id": "a", "algebra": "A1"}, {"id": "b", "algebra": "A2"}],
             "hypermultiplets": [{"representations": {"a": "fundamental", "b": "fundamental"}, "number": 2}]},
            {"gauge_groups": [{"id": "a", "algebra": "A1"}, {"id": "b", "algebra": "A2"}],
             "hypermultiplets": [{"representations": {"a": "fundamental"}, "number": 4},
                                  {"representations": {"b": "fundamental"}, "number": 6},
                                  {"representations": {}, "number": 2}]}]
        for data in cases:
            with self.subTest(data=data):
                expected = self.calculate(data, backend="python", processes=1)
                for workers in (1, 4, 32):
                    self.assertEqual(self.calculate(data, worker_count=workers), expected)

    def test_worker_count_overrides_legacy_form_threads(self):
        native = idx._require_native_index()
        with patch.object(native, "expand_and_project", wraps=native.expand_and_project) as call:
            self.calculate(form_threads=4)
            self.assertEqual(call.call_args.args[-2], 4)
            self.calculate(form_threads=4, worker_count=2)
            self.assertEqual(call.call_args.args[-2], 2)

    def test_invalid_backend_and_worker_counts_fail_before_cache_access(self):
        for options in ({"backend": "auto"}, {"worker_count": 0}, {"worker_count": -1},
                        {"worker_count": True}, {"worker_count": 2.0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.calculate(order=0, **options)
        self.assertFalse(self.directory.joinpath("characters.db").exists())

    def test_zero_timeout_errors_and_later_calls_recover(self):
        with self.assertRaisesRegex(RuntimeError, "index expansion timed out"):
            self.calculate(timeout=0, worker_count=4)
        self.assertFalse(self.directory.joinpath("characters.db").exists())
        self.assertTrue(self.calculate(timeout=None))

    def test_missing_or_outdated_extension_gives_build_instructions(self):
        missing = ModuleNotFoundError("not built", name="index._char_cache_cpp")
        for outcome in (missing, object()):
            with self.subTest(outcome=outcome), patch.object(idx, "import_module",
                 side_effect=outcome if isinstance(outcome, Exception) else None,
                 return_value=outcome), self.assertRaisesRegex(RuntimeError, "build_char_cache.py"):
                self.calculate()
        with patch.object(idx, "_require_native_index", side_effect=AssertionError("loaded native")):
            self.assertEqual(self.calculate(order=1), 1)

    def test_independent_python_threads_can_call_native(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.calculate(worker_count=2), range(6)))
        self.assertTrue(all(value == results[0] for value in results))

    def test_binding_strict_conversion_and_large_exact_coefficients(self):
        native = idx._require_native_index()
        cache = native.CharacterDecompositionCache(str(self.directory / "binding.db"))
        self.addCleanup(cache.close)
        huge = 10 ** 130 + 3
        value = native.expand_and_project(4, [], [], [], {(): huge}, cache)
        self.assertEqual(value[(4, 0, -2)], Fraction(huge * (huge + 1), 2))
        self.assertTrue(all(isinstance(coefficient, Fraction) for coefficient in value.values()))
        for order, vectors, matter, specs, workers in (
            (True, [], {}, [], 1), (4, [0.0], {}, [(0, (1,))], 1),
            (4, [], {(0,): 1.0}, [(0, (1,))], 1), (4, [], {}, [(True, (1,))], 1),
            (4, [], {}, [], 1.0)):
            with self.subTest(order=order, vectors=vectors, matter=matter), self.assertRaises((TypeError, ValueError)):
                native.expand_and_project(order, ["A1"], specs, vectors, matter, cache, workers)


if __name__ == "__main__":
    unittest.main()
