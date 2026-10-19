"""Production native pipeline, exact compatibility, and selection contracts."""
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from index import n2_theory_index as idx
from index import _index_calculation_native as native_loader

SU2 = {"algebra": "A1", "hypermultiplets": [{"representation": "fundamental", "number": 4}]}
SU3 = {"algebra": "A2", "hypermultiplets": [{"representation": "fundamental", "number": 6}]}


class NativeLoaderTests(unittest.TestCase):
    def test_python_cache_selection_does_not_disable_native_index(self):
        with patch.dict(os.environ, {"N2SCFTDB_CHAR_CACHE_BACKEND": "python"}), \
             patch.object(native_loader, "import_module", wraps=native_loader.import_module) as load:
            self.assertIsNone(native_loader.load_native())
            load.assert_not_called()
            native = native_loader.load_native(require_index=True)
            self.assertTrue(callable(native.expand_and_project))
            load.assert_called_once_with("index._index_calculation_cpp")

    def test_missing_extension_only_permits_automatic_cache_fallback(self):
        missing = ModuleNotFoundError("not built", name="index._index_calculation_cpp")
        with patch.object(native_loader, "import_module", side_effect=missing):
            with patch.dict(os.environ, {"N2SCFTDB_CHAR_CACHE_BACKEND": "auto"}):
                self.assertIsNone(native_loader.load_native())
            with patch.dict(os.environ, {"N2SCFTDB_CHAR_CACHE_BACKEND": "cpp"}), \
                 self.assertRaisesRegex(ImportError, "build_index_calculation.py") as error:
                native_loader.load_native()
            self.assertIs(error.exception.__cause__, missing)
            with patch.dict(os.environ, {"N2SCFTDB_CHAR_CACHE_BACKEND": "python"}), \
                 self.assertRaisesRegex(RuntimeError, "build_index_calculation.py") as error:
                native_loader.load_native(require_index=True)
            self.assertIs(error.exception.__cause__, missing)

    def test_dependency_and_binary_errors_remain_visible(self):
        errors = (ModuleNotFoundError("missing dependency", name="native_dependency"),
                  ImportError("undefined symbol"))
        for backend in ("auto", "cpp"):
            for require_index in (False, True):
                for failure in errors:
                    with self.subTest(backend=backend, require_index=require_index, error=failure), \
                         patch.dict(os.environ, {"N2SCFTDB_CHAR_CACHE_BACKEND": backend}), \
                         patch.object(native_loader, "import_module", side_effect=failure), \
                         self.assertRaises(type(failure)) as error:
                        native_loader.load_native(require_index=require_index)
                    self.assertIs(error.exception, failure)


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
        native = native_loader.load_native(require_index=True)
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
        missing = ModuleNotFoundError("not built", name="index._index_calculation_cpp")
        for outcome in (missing, object()):
            with self.subTest(outcome=outcome), patch.object(native_loader, "import_module",
                 side_effect=outcome if isinstance(outcome, Exception) else None,
                 return_value=outcome), self.assertRaisesRegex(RuntimeError, "build_index_calculation.py"):
                self.calculate()
        with patch.object(idx, "load_native", side_effect=AssertionError("loaded native")):
            self.assertEqual(self.calculate(order=1), 1)

    def test_independent_python_threads_can_call_native(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.calculate(worker_count=2), range(6)))
        self.assertTrue(all(value == results[0] for value in results))

    def test_binding_strict_conversion_and_large_exact_coefficients(self):
        native = native_loader.load_native(require_index=True)
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
