"""FORM/TFORM execution, exact results and cache reuse across thread counts."""

from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common.form_utils import run_form
from index.form_expansion_cache import FormExpansionCache
from index.n2_theory_index import calculate_index
from index.n2_theory_coulomb_branches import (
    calculate_coulomb_branch_index, extract_coulomb_branch_spectrum_from_index,
)


class FormCommandTests(unittest.TestCase):
    def test_serial_and_threaded_commands_preserve_paths_with_spaces(self):
        for threads, expected in ((1, ["/custom tools/form"]),
                                  (2, ["/custom tools/tform", "-w2"]),
                                  (32, ["/custom tools/tform", "-w32"])):
            with self.subTest(threads=threads), patch(
                "common.form_utils.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "result = 1;", ""),
            ) as execute:
                output = run_form("L result=1;\n.end", form_executable="/custom tools/form",
                                  tform_executable="/custom tools/tform", form_threads=threads,
                                  timeout=7)
                command = execute.call_args.args[0]
                self.assertEqual(command[:-1], expected + ["-q"])
                self.assertEqual(execute.call_args.kwargs["timeout"], 7)
                self.assertEqual(output, "result = 1;")
                self.assertFalse(Path(command[-1]).exists())

    def test_invalid_counts_fail_before_external_execution_or_cache_access(self):
        for threads in (0, -1, True, 1.5, 2.0):
            with self.subTest(threads=threads), patch("common.form_utils.subprocess.run") as execute:
                with self.assertRaises(ValueError):
                    run_form("", form_executable="form", timeout=1, form_threads=threads)
                with self.assertRaises(ValueError):
                    FormExpansionCache(form_threads=threads)
                execute.assert_not_called()

    def test_threaded_failures_name_the_selected_executable(self):
        for error, message in ((FileNotFoundError(), "TFORM executable '/missing/tform'"),
                               (subprocess.TimeoutExpired("tform", 1), "TFORM calculation timed out")):
            with self.subTest(error=error), patch("common.form_utils.subprocess.run", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, message):
                    run_form("", form_executable="form", tform_executable="/missing/tform",
                             form_threads=2, timeout=1)


@unittest.skipUnless(shutil.which("form") and shutil.which("tform") and shutil.which("lie"),
                     "FORM, TFORM and LiE required")
class ThreadedIndexTests(unittest.TestCase):
    def test_real_tform_matches_serial_for_connected_and_disconnected_indices(self):
        cases = (
            {"algebra": "A1", "hypermultiplets": [
                {"representation": "fundamental", "number": 4}]},
            {"gauge_groups": [{"id": "a", "algebra": "A1"}, {"id": "b", "algebra": "A1"}],
             "hypermultiplets": [{"representations": {"a": "fundamental"}, "number": 4},
                                  {"representations": {"b": "fundamental"}, "number": 4},
                                  {"representations": {}, "number": 1}]},
        )
        for data in cases:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                options = dict(char_cache_database_path=root/"characters.sqlite", processes=1)
                serial = calculate_index(data, 8, form_cache_database_path=root/"serial.sqlite",
                                         tform_executable="/missing/tform", **options)
                threaded = calculate_index(data, 8, form_cache_database_path=root/"threaded.sqlite",
                                           form_executable="/missing/form", form_threads=2, **options)
                self.assertEqual(serial, threaded)
                # Changing the engine/count must reuse the same raw-program entries.
                with patch("index.form_expansion_cache.run_form", side_effect=AssertionError("cache miss")):
                    cached = calculate_index(data, 8, form_cache_database_path=root/"threaded.sqlite",
                                             form_executable="/missing/form", form_threads=1, **options)
                self.assertEqual(serial, cached)

    def test_threaded_coulomb_pe_and_pl_retain_exact_fractional_dimensions(self):
        spectrum = (Fraction(6, 5), Fraction(2), Fraction(2))
        serial = calculate_coulomb_branch_index(spectrum, 6, tform_executable="/missing/tform")
        threaded = calculate_coulomb_branch_index(spectrum, 6, form_executable="/missing/form",
                                                 form_threads=2)
        self.assertEqual(serial, threaded)
        extracted = extract_coulomb_branch_spectrum_from_index(
            threaded, 6, form_executable="/missing/form", form_threads=2,
        )
        self.assertEqual(extracted, spectrum)


if __name__ == "__main__":
    unittest.main()
