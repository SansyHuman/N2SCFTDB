from fractions import Fraction
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from index.char_decomposition_cache import (
    CharacterDecompositionCache,
    parse_lie_decomposition,
)
from index.n2_theory_index import (
    _matter_character_multiplicities,
    _parse_input,
    _to_sage_polynomial,
    calculate_index,
    parse_index_polynomial,
)


HAS_EXTERNAL_BACKEND = (
    shutil.which("form") is not None and shutil.which("lie") is not None
)


class LieDecompositionParserTests(unittest.TestCase):
    def test_parses_canonical_lie_output_with_whitespace(self):
        self.assertEqual(
            parse_lie_decomposition("-1X[0] + 1X[2]\n", 1),
            {(0,): -1, (2,): 1},
        )

    def test_normalizes_adjacent_signs_emitted_by_lie(self):
        self.assertEqual(
            parse_lie_decomposition("1X[0,3] +-1X[1,1]", 2),
            {(0, 3): 1, (1, 1): -1},
        )


@unittest.skipUnless(HAS_EXTERNAL_BACKEND, "FORM and LiE are required")
class CharacterDecompositionCacheTests(unittest.TestCase):
    def test_cache_stores_cartan_and_dynkin_keys_in_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CharacterDecompositionCache(directory)
            self.addCleanup(cache.close)
            decomposition = cache.get_decomposition("A1", (1,), (0, 1))

            expected_path = Path(directory) / "char_decomposition_cache.db"
            self.assertTrue(expected_path.is_file())
            self.assertEqual(decomposition, {(0,): -1, (2,): 1})

            with sqlite3.connect(expected_path) as connection:
                row = connection.execute(
                    "SELECT algebra, dynkin_labels, adams_order FROM character_decompositions"
                ).fetchone()
            self.assertEqual(row, ("A1", "[1]", 2))
            cache.close()

            file_only = CharacterDecompositionCache(
                directory, lie_executable="missing-lie-for-cache-test"
            )
            self.addCleanup(file_only.close)
            self.assertEqual(
                file_only.get_decomposition("A1", (1,), (0, 1)),
                decomposition,
            )

    def test_parallel_batch_preserves_entries_that_share_one_database(self):
        with tempfile.TemporaryDirectory() as directory:
            requests = [
                ("A1", (1,), (0, 3)),
                ("A1", (1,), (1, 1, 1)),
                ("A1", (1,), (2, 0, 0, 1)),
            ]
            with CharacterDecompositionCache(
                Path(directory) / "sequential", max_workers=1
            ) as sequential_cache:
                sequential = sequential_cache.get_decompositions(requests)
            cache = CharacterDecompositionCache(
                Path(directory) / "parallel", max_workers=3
            )
            self.addCleanup(cache.close)
            parallel = cache.get_decompositions(requests)
            self.assertEqual(parallel, sequential)

            with sqlite3.connect(cache.database_path) as connection:
                rows = connection.execute(
                    "SELECT adams_powers FROM character_decompositions WHERE adams_order=6"
                ).fetchall()
            self.assertEqual(
                {tuple(json.loads(row[0])) for row in rows},
                {request[2] + (0,) * (6 - len(request[2])) for request in requests},
            )
            cache.close()

            file_only = CharacterDecompositionCache(
                Path(directory) / "parallel",
                lie_executable="missing-lie-for-parallel-cache-test",
                max_workers=3,
            )
            self.addCleanup(file_only.close)
            self.assertEqual(file_only.get_decompositions(requests), parallel)


@unittest.skipUnless(HAS_EXTERNAL_BACKEND, "FORM and LiE are required")
class N2TheoryIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.char_cache_database_path = Path(cls.temporary_directory.name) / "characters.db"

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def test_cli_file_and_module_use_character_database_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            theory = root / "theory.json"
            theory.write_text(json.dumps({"algebra": "A1", "hypermultiplets": []}))
            for entrypoint in ([str(PROJECT_ROOT / "index/n2_theory_index.py")],
                               ["-m", "index.n2_theory_index"]):
                with self.subTest(entrypoint=entrypoint):
                    database = root / f"cache {len(entrypoint)}" / "custom.sqlite"
                    result = subprocess.run(
                        [sys.executable, *entrypoint, str(theory), "--order", "4",
                         "--char-cache-database", str(database), "--processes", "1"],
                        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(parse_index_polynomial(result.stdout),
                                     parse_index_polynomial("1 + t^4*u^4"))
                    self.assertTrue(database.is_file())
                    self.assertTrue(database.with_name("form_expansion_cache.db").is_file())
                    self.assertFalse(database.with_name("char_decomposition_cache.db").exists())

    def test_su3_six_flavors_matches_reference_through_t6(self):
        result = calculate_index(
            {
                "algebra": "A2",
                "hypermultiplets": [
                    {
                        "representation": "fundamental",
                        "number": 6,
                        "kind": "full",
                    }
                ],
            },
            6,
            char_cache_database_path=self.char_cache_database_path,
        )
        t, y, u = result.parent().gens()
        expected = (
            1
            + (36 / u**2 + u**4) * t**4
            - u**2 * (y + 1 / y) * t**5
            + (40 / u**3 - 36 + u**6) * t**6
        )
        self.assertEqual(result, expected)

    def test_named_and_explicit_dynkin_labels_agree(self):
        named = {
            "algebra": "A1",
            "hypermultiplets": [
                {"representation": "fundamental", "number": 4}
            ],
        }
        labelled = {
            "algebra": "A1",
            "hypermultiplets": [
                {"dynkin_labels": [1], "number": 4}
            ],
        }
        self.assertEqual(
            calculate_index(named, 6, char_cache_database_path=self.char_cache_database_path),
            calculate_index(labelled, 6, char_cache_database_path=self.char_cache_database_path),
        )

    def test_product_bifundamental_matches_reference_through_t10(self):
        result = calculate_index(
            {
                "gauge_groups": [
                    {"id": "left", "algebra": "A1"},
                    {"id": "right", "algebra": "A1"},
                ],
                "hypermultiplets": [
                    {
                        "representations": {
                            "left": "fundamental",
                            "right": "fundamental",
                        },
                        "number": 2,
                        "kind": "full",
                    }
                ],
            },
            10,
            char_cache_database_path=self.char_cache_database_path,
        )
        t, y, u = result.parent().gens()
        expected = (
            1
            + (2 * u**4 + 10 / u**2) * t**4
            - 2 * u**2 * (y + 1 / y) * t**5
            - 4 * t**6
            + (2 * u**4 + 6 / u**2) * (y + 1 / y) * t**7
            + (
                3 * u**8
                - 2 * u**2 * (y**2 + y ** (-2) - 1)
                + 44 / u**4
            )
            * t**8
            - (4 * u**6 + 10) * (y + 1 / y) * t**9
            + (
                3 * u**4 * (y**2 + y ** (-2))
                + (6 * (y**2 + y ** (-2)) - 52) / u**2
            )
            * t**10
        )
        self.assertEqual(result, expected)

    def test_decoupled_product_equals_product_of_simple_indices(self):
        left = {
            "algebra": "A1",
            "hypermultiplets": [
                {"representation": "fundamental", "number": 4}
            ],
        }
        right = {
            "algebra": "A2",
            "hypermultiplets": [
                {"representation": "fundamental", "number": 6}
            ],
        }
        product = {
            "gauge_groups": [
                {"id": "left", "algebra": "A1"},
                {"id": "right", "algebra": "A2"},
            ],
            "hypermultiplets": [
                {
                    "representations": {"left": "fundamental"},
                    "number": 4,
                },
                {
                    "representations": {"right": "fundamental"},
                    "number": 6,
                },
            ],
        }
        order = 6
        actual = calculate_index(
            product, order, char_cache_database_path=self.char_cache_database_path
        )
        independent = calculate_index(
            left, order, char_cache_database_path=self.char_cache_database_path
        ) * calculate_index(
            right, order, char_cache_database_path=self.char_cache_database_path
        )
        t, y, u = actual.parent().gens()
        expected = sum(
            (
                coefficient * t**powers[0] * y**powers[1] * u**powers[2]
                for powers, coefficient in independent.dict().items()
                if powers[0] <= order
            ),
            actual.parent().zero(),
        )
        self.assertEqual(actual, expected)

    def test_full_product_hyper_adds_only_representation_and_its_dual(self):
        factors, hypers = _parse_input(
            {
                "gauge_groups": [
                    {"id": "left", "algebra": "A2"},
                    {"id": "right", "algebra": "A2"},
                ],
                "hypermultiplets": [
                    {
                        "representations": {
                            "left": "fundamental",
                            "right": "fundamental",
                        },
                        "kind": "full",
                    }
                ],
            }
        )
        self.assertEqual(
            _matter_character_multiplicities(factors, hypers),
            {
                ((0, (1, 0)), (1, (1, 0))): 1,
                ((0, (0, 1)), (1, (0, 1))): 1,
            },
        )

    def test_valid_product_half_hyper_is_supported(self):
        result = calculate_index(
            {
                "gauge_groups": [
                    {"id": "su2", "algebra": "A1"},
                    {"id": "g2", "algebra": "G2"},
                ],
                "hypermultiplets": [
                    {
                        "representations": {
                            "su2": "fundamental",
                            "g2": "fundamental",
                        },
                        "kind": "half",
                    }
                ],
            },
            4,
            char_cache_database_path=self.char_cache_database_path,
        )
        self.assertEqual(
            result.monomial_coefficient(result.parent().one()), 1
        )

    def test_sage_polynomial_is_a_flat_sum_of_monomials(self):
        result = _to_sage_polynomial(
            {
                (4, 0, 4): Fraction(1),
                (4, 0, -2): Fraction(36),
            }
        )

        self.assertEqual(result.parent().variable_names(), ("t", "y", "u"))
        self.assertNotIn("(", str(result))
        self.assertNotIn(")", str(result))

    def test_serialized_index_round_trip(self):
        original = _to_sage_polynomial(
            {
                (0, 0, 0): Fraction(1),
                (4, 0, -2): Fraction(36),
                (5, 1, 2): Fraction(-1),
                (6, 0, -3): Fraction(7, 3),
            }
        )

        restored = parse_index_polynomial(str(original))

        self.assertEqual(restored, original)
        self.assertIs(restored.parent(), original.parent())

    def test_index_parser_rejects_non_polynomial_text(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            parse_index_polynomial("__import__('os')")

    def test_three_factor_projection_is_independent_of_factor_order(self):
        factor_ids = ("first", "second", "third")

        def theory(order):
            return {
                "gauge_groups": [
                    {"id": factor_id, "algebra": "A1"}
                    for factor_id in order
                ],
                "hypermultiplets": [
                    {
                        "representations": {
                            factor_id: "fundamental"
                            for factor_id in factor_ids
                        },
                        "kind": "half",
                    }
                ],
            }

        self.assertEqual(
            calculate_index(
                theory(factor_ids),
                6,
                char_cache_database_path=self.char_cache_database_path,
            ),
            calculate_index(
                theory(reversed(factor_ids)),
                6,
                char_cache_database_path=self.char_cache_database_path,
            ),
        )

    def test_orders_below_first_letter_need_no_external_process(self):
        data = {
            "gauge_groups": [
                {"id": "left", "algebra": "A1"},
                {"id": "right", "algebra": "A1"},
            ],
            "hypermultiplets": [],
        }
        for order in (0, 1):
            with self.subTest(order=order):
                result = calculate_index(
                    data,
                    order,
                    char_cache_database_path=self.char_cache_database_path,
                    lie_executable="missing-lie-for-low-order-test",
                    form_executable="missing-form-for-low-order-test",
                )
                self.assertEqual(result, 1)

    def test_invalid_half_hyper_uses_existing_validation(self):
        with self.assertRaisesRegex(ValueError, "pseudoreal"):
            calculate_index(
                {
                    "algebra": "A2",
                    "hypermultiplets": [
                        {
                            "representation": "fundamental",
                            "kind": "half",
                        }
                    ],
                },
                4,
                char_cache_database_path=self.char_cache_database_path,
            )

    def test_order_must_be_nonnegative(self):
        with self.assertRaisesRegex(ValueError, "order"):
            calculate_index(
                {"algebra": "A1", "hypermultiplets": []},
                -1,
                char_cache_database_path=self.char_cache_database_path,
            )


if __name__ == "__main__":
    unittest.main()
