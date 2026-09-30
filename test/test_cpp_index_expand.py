"""Compare every exact C++ expansion term with uncached, real FORM output.

Run from the repository root with ``sage -python -B -m unittest
test.test_cpp_index_expand -v``. See test/cpp/README.md for compiler and GMP
configuration. The executable and all FORM scripts live in temporary folders;
no Python extension, character decomposition, database or cache is used.
"""

from fractions import Fraction
import json
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from common.form_utils import run_form
from index import n2_theory_index as idx
from index.form_expansion_cache import FormExpansionCache, IndexFormTerm


ROOT = Path(__file__).resolve().parents[1]
FORM = os.environ.get("FORM_EXECUTABLE", "form")
HAS_FORM = shutil.which(FORM) is not None


class CppIndexExpandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shlex.split(os.environ.get("CXX", "/usr/bin/g++"))
        if not compiler or shutil.which(compiler[0]) is None:
            raise unittest.SkipTest("a C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="cpp-index-expand-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.executable = Path(cls.directory.name) / "index_expand_driver"
        include = os.environ.get("GMP_INCLUDE_DIR")
        library = os.environ.get("GMP_LIBRARY_DIR")
        # Sage often provides development files when only GMP runtime packages
        # are installed system-wide. Explicit environment settings take priority.
        if include is None and (Path(sys.prefix) / "include/gmpxx.h").is_file():
            include = str(Path(sys.prefix) / "include")
            if library is None:
                library = str(Path(sys.prefix) / "lib")
        command = [*compiler, "-std=c++17", "-O2", "-pthread", "-Wall", "-Wextra", "-Wpedantic"]
        command += shlex.split(os.environ.get("CPP_INDEX_CXXFLAGS", ""))
        command += ["-I", str(ROOT)]
        if include:
            command += ["-I", include]
        command += [str(ROOT / "test/cpp/index_expand_driver.cpp"),
                    str(ROOT / "index/cpp/index_expand.cpp")]
        if library:
            command += ["-L", library, f"-Wl,-rpath,{library}"]
        command += ["-lgmpxx", "-lgmp", "-o", str(cls.executable)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f"C++ test build failed:\n{result.stdout}\n{result.stderr}")

    def cpp_expansion(self, order, character_count, vectors, matter, worker_count=None):
        lines = [f"{order} {character_count} {len(vectors)}",
                 " ".join(str(character) for character in vectors), str(len(matter))]
        for monomial, multiplicity in matter.items():
            lines.append(" ".join(map(str, (multiplicity, len(monomial), *monomial))))
        command = [str(self.executable)]
        if worker_count is not None:
            command += ["--workers", str(worker_count)]
        result = subprocess.run(command, input="\n".join(lines) + "\n",
                                text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        terms = []
        for line in result.stdout.splitlines():
            fields = list(map(int, line.split()))
            self.assertGreaterEqual(len(fields), 6)
            t, y, u, numerator, denominator, count = fields[:6]
            self.assertGreater(denominator, 0)
            characters = []
            position = 6
            for _ in range(count):
                character, length = fields[position:position + 2]
                position += 2
                powers = tuple(fields[position:position + length])
                self.assertEqual(len(powers), length)
                self.assertEqual(length, sum(j * power for j, power in enumerate(powers, 1)))
                self.assertTrue(all(power >= 0 for power in powers))
                self.assertTrue(0 <= character < character_count)
                self.assertTrue(length > 0)
                characters.append((character, powers))
                position += length
            self.assertEqual(position, len(fields), "unexpected C++ output fields")
            self.assertEqual(characters, sorted(characters))
            self.assertEqual(len(characters), len({character for character, _ in characters}))
            self.assertTrue(0 <= t <= order)
            coefficient = Fraction(numerator, denominator)
            self.assertNotEqual(coefficient, 0)
            self.assertEqual((coefficient.numerator, coefficient.denominator),
                             (numerator, denominator), "C++ coefficient is not canonical")
            terms.append(IndexFormTerm(coefficient, t, y, u, tuple(characters)))
        self.assertTrue(terms, "vacuum term must always be present")
        keys = [(term.t_power, term.y_power, term.u_power, term.characters) for term in terms]
        self.assertEqual(keys, sorted(keys), "C++ output ordering is not canonical")
        return terms

    def term_map(self, terms):
        result = {(term.t_power, term.y_power, term.u_power, term.characters): term.coefficient
                  for term in terms}
        self.assertEqual(len(result), len(terms), "duplicate formal monomials")
        return result

    def assert_matches_form(self, order, character_count, vectors, matter):
        output = run_form(
            idx._build_form_program(order, character_count, tuple(vectors), matter),
            form_executable=FORM, timeout=120)
        # FORM inserts backslash-newline continuations within very long integer
        # literals. Remove only this display wrapping before the existing parser.
        output = re.sub(r"\\\r?\n\s*", "", output)
        expected = FormExpansionCache.parse_form_output(output)
        expected_map = self.term_map(expected)
        serial = None
        for workers in (None, 1, 2, 4):
            with self.subTest(workers=workers):
                actual = self.cpp_expansion(order, character_count, vectors, matter,
                                            worker_count=workers)
                self.assertEqual(self.term_map(actual), expected_map)
                if serial is None:
                    serial = actual
                else:
                    self.assertEqual(actual, serial, "parallel result differs from serial ordering")

    def test_native_invalid_inputs(self):
        result = subprocess.run([str(self.executable), "--self-test"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "invalid-input checks passed")

    def test_native_concurrent_calls_and_repeatability(self):
        result = subprocess.run([str(self.executable), "--parallel-self-test"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "parallel-call checks passed")

    def test_worker_count_larger_than_small_workload(self):
        for arguments in ((0, 0, (), {}), (4, 1, (0,), {(0,): 2})):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.cpp_expansion(*arguments, worker_count=32),
                                 self.cpp_expansion(*arguments))

    def test_cutoffs_below_first_letter_return_only_vacuum(self):
        vacuum = [IndexFormTerm(Fraction(1), 0, 0, 0, ())]
        for order in (0, 1):
            for arguments in ((0, (), {}), (2, (0,), {(1,): 8}), (0, (), {(): 3})):
                with self.subTest(order=order, arguments=arguments):
                    self.assertEqual(self.cpp_expansion(order, *arguments), vacuum)
                    if HAS_FORM:
                        self.assert_matches_form(order, *arguments)

    @unittest.skipUnless(HAS_FORM, "FORM is required for exact comparisons")
    def test_existing_42_form_regression_cases(self):
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
            for order in (2, 3, 4, 5, 8, 9):
                with self.subTest(case=name, order=order):
                    self.assert_matches_form(order, *arguments)

    @unittest.skipUnless(HAS_FORM, "FORM is required for exact comparisons")
    def test_default_cutoff_simple_product_and_trifundamental(self):
        data = json.loads((ROOT / "anomalies/example_product_bifundamental.json").read_text())
        factors, hypers = idx._parse_input(data)
        specs, vectors, matter = idx._character_basis(factors, hypers)
        cases = {
            "simple": (2, (0,), {(1,): 8}),
            "product_fixture": (len(specs), vectors, matter),
            "trifundamental": (6, (0, 1, 2), {(3, 4, 5): 1}),
        }
        for name, arguments in cases.items():
            with self.subTest(case=name, order=18):
                self.assert_matches_form(18, *arguments)

    @unittest.skipUnless(HAS_FORM, "FORM is required for exact comparisons")
    def test_larger_parallel_trifundamental_expansion(self):
        self.assert_matches_form(20, 6, (0, 1, 2), {(3, 4, 5): 1})

    @unittest.skipUnless(HAS_FORM, "FORM is required for exact comparisons")
    def test_signed_zero_repeated_and_large_integer_inputs(self):
        cases = {
            "unused_characters": (9, 5, (), {}),
            "zero_matter": (9, 3, (0,), {(1,): 0, (1, 2): 0}),
            "signed_free": (10, 0, (), {(): -3}),
            "signed_charged": (10, 2, (0,), {(1,): -2, (): 1}),
            "repeated_vectors": (10, 2, (0, 1, 0), {(1,): 2}),
            "repeated_matter_characters": (10, 2, (0,), {(1, 1): 3}),
            "equivalent_unsorted_monomials": (9, 3, (), {(2, 1): 2, (1, 2): 3}),
            "exact_cancellation": (10, 2, (), {(0, 1): 3, (1, 0): -3}),
            "large_positive": (8, 1, (), {(0,): 2**100 + 123}),
            "large_negative": (8, 0, (), {(): -(2**100 + 321)}),
            "free_and_charged": (11, 3, (2,), {(): 2, (0, 1): 1, (2,): -1}),
        }
        for name, arguments in cases.items():
            with self.subTest(case=name):
                self.assert_matches_form(*arguments)

    @unittest.skipUnless(HAS_FORM, "FORM is required for exact comparisons")
    def test_deterministic_random_formal_inputs(self):
        generator = random.Random(20260929)
        for case in range(16):
            character_count = generator.randrange(5)
            order = generator.randrange(2, 12)
            vectors = tuple(generator.randrange(character_count)
                            for _ in range(generator.randrange(4))) if character_count else ()
            matter = {}
            for _ in range(generator.randrange(5)):
                monomial = tuple(generator.randrange(character_count)
                                 for _ in range(generator.randrange(4))) if character_count else ()
                matter[monomial] = generator.randrange(-3, 5)
            with self.subTest(case=case, order=order, characters=character_count):
                self.assert_matches_form(order, character_count, vectors, matter)


if __name__ == "__main__":
    unittest.main()
