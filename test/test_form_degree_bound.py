"""Exact FORM regressions for multiplication with a remaining degree budget."""

import json
from pathlib import Path
import shutil
import unittest

from common.form_utils import run_form
from index import n2_theory_index as idx
from index.form_expansion_cache import FormExpansionCache
from test.benchmark_product_form import build_baseline_program


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("form"), "FORM is required")
class DegreeBoundedFormTests(unittest.TestCase):
    def expansion(self, builder, order, character_count, vectors, matter):
        program = builder(order, character_count, vectors, matter)
        output = run_form(program, form_executable="form", timeout=30)
        return FormExpansionCache.parse_form_output(output)

    def test_complete_formal_expansion_matches_unrestricted_multiplication(self):
        # Include empty/free sectors, pure vectors, signed coefficients,
        # shared characters and matter charged under several gauge factors.
        cases = {
            "empty": (0, (), {}),
            "free_hypers": (0, (), {(): 2}),
            "pure_vector": (1, (0,), {}),
            "simple": (2, (0,), {(1,): 8}),
            "adjoint_and_free": (1, (0,), {(0,): 2, (): 1}),
            "bifundamental": (4, (0, 1), {(2, 3): 4}),
            "trifundamental": (6, (0, 1, 2), {(3, 4, 5): 1}),
        }
        for name, arguments in cases.items():
            # Orders 2/3 bypass the loop; 4 starts it; odd cutoffs and
            # multiple steps exercise terminal degrees and marker handling.
            for order in (2, 3, 4, 5, 8, 9):
                with self.subTest(case=name, order=order):
                    expected = self.expansion(build_baseline_program, order, *arguments)
                    actual = self.expansion(idx._build_form_program, order, *arguments)
                    self.assertEqual(actual, expected)
                    self.assertTrue(all(0 <= term.t_power <= order for term in actual))

    def test_connected_product_matches_through_default_cutoff(self):
        data = json.loads((ROOT / "anomalies/example_product_bifundamental.json").read_text())
        factors, hypers = idx._parse_input(data)
        specs, vectors, matter = idx._character_basis(factors, hypers)
        expected = self.expansion(build_baseline_program, 18, len(specs), vectors, matter)
        actual = self.expansion(idx._build_form_program, 18, len(specs), vectors, matter)
        self.assertEqual(actual, expected)
        self.assertEqual(max(term.t_power for term in actual), 18)


if __name__ == "__main__":
    unittest.main()
