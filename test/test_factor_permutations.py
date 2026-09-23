from copy import deepcopy
from collections import Counter
import hashlib
from itertools import permutations, product
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from common import n2_theory_db as db
from common.n2_theory_iter import enumerate_product_theory_candidates
from index.n2_theory_index import _parse_input


MIXED_PRODUCT = {
    "gauge_groups": [{"id": "a", "algebra": "A1"}, {"id": "b", "algebra": "A2"}],
    "hypermultiplets": [
        {"representations": {"a": "fundamental", "b": "fundamental"}},
        {"representations": {"a": "fundamental"}},
        {"representations": {"b": "fundamental"}, "number": 4},
    ],
}


def reordered(data, order, *, rename=False):
    result = deepcopy(data)
    result["gauge_groups"] = [result["gauge_groups"][i] for i in order]
    if rename:
        names = {factor["id"]: f"renamed_{i}" for i, factor in enumerate(result["gauge_groups"])}
        for factor in result["gauge_groups"]:
            factor["id"] = names[factor["id"]]
        for hyper in result["hypermultiplets"]:
            hyper["representations"] = {names[key]: value for key, value in hyper["representations"].items()}
    return result


def su2_quiver(size, edges):
    """Conformal SU(2) quiver with fundamentals filling the remaining budget."""
    degrees = [0] * size
    hypers = []
    for left, right in edges:
        degrees[left] += 1
        degrees[right] += 1
        hypers.append({"representations": {f"g{left}": [1], f"g{right}": [1]}})
    for node, degree in enumerate(degrees):
        if degree < 2:
            hypers.append({"representations": {f"g{node}": [1]}, "number": 4 - 2 * degree})
    return {"gauge_groups": [{"id": f"g{i}", "algebra": "A1"} for i in range(size)],
            "hypermultiplets": hypers}


def checked_hash(data):
    checked = db.check_input_data(data)
    if not checked["lagrangian_scft_candidate"]:
        raise AssertionError("expected a conformal fixture")
    return db._canonical_hash(checked)


class FactorPermutationHashTests(unittest.TestCase):
    def test_two_su2_factors_have_eight_candidates_but_six_theories(self):
        candidates = enumerate_product_theory_candidates(["A1", "A1"], only_one_sector=False)
        self.assertEqual(len(candidates), 8)
        multiplicities = Counter(checked_hash(data) for data in candidates)
        self.assertEqual(sorted(multiplicities.values()), [1, 1, 1, 1, 2, 2])

    def test_different_cartan_types_have_one_hash_and_legacy_aliases(self):
        hashes = []
        for order in ((0, 1), (1, 0)):
            for rename in (False, True):
                data = reordered(MIXED_PRODUCT, order, rename=rename)
                hashes.append(db._canonical_hashes(db.check_input_data(data)))
        self.assertTrue(all(row == hashes[0] for row in hashes))
        self.assertEqual({h[:12] for h in hashes[0]}, {"3c94753090a8", "e9ffaa51cab6"})
        self.assertEqual(hashes[0][0][:12], "3c94753090a8")

    def test_all_orders_of_repeated_factors_preserve_the_complete_quiver(self):
        data = su2_quiver(4, [(0, 1), (1, 2), (2, 3)])
        original = deepcopy(data)
        hashes = {checked_hash(reordered(data, order, rename=True))
                  for order in permutations(range(4))}
        self.assertEqual(len(hashes), 1)
        self.assertEqual(data, original)

    def test_identical_local_signatures_do_not_merge_different_quivers(self):
        # Every factor has two identical bifundamental neighbours in both cases.
        # A six-cycle and two triangles nevertheless have different connectivity.
        cycle = su2_quiver(6, [(i, (i + 1) % 6) for i in range(6)])
        triangles = su2_quiver(6, [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)])
        cycle_hash, triangles_hash = checked_hash(cycle), checked_hash(triangles)
        self.assertNotEqual(cycle_hash, triangles_hash)
        for order in ((5, 4, 3, 2, 1, 0), (2, 5, 0, 4, 1, 3)):
            self.assertEqual(checked_hash(reordered(cycle, order)), cycle_hash)
            self.assertEqual(checked_hash(reordered(triangles, order)), triangles_hash)

    def test_permutations_and_triality_are_canonicalized_together(self):
        hashes = set()
        for labels in ((1, 0, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)):
            for kind, number in (("full", 1), ("half", 2)):
                data = {
                    "gauge_groups": [{"id": "d", "algebra": "D4"},
                                     {"id": "a", "algebra": "A1"}],
                    "hypermultiplets": [
                        {"representations": {"d": list(labels), "a": [1]}, "kind": "half"},
                        {"representations": {"d": list(labels)}, "number": 5},
                        {"representations": {"a": [0]}, "number": 1},
                    ],
                }
                # Test a pseudoreal single-factor sector in the same product too.
                data["gauge_groups"].append({"id": "b", "algebra": "A1"})
                data["hypermultiplets"].append({"representations": {"b": [1]},
                                              "kind": kind, "number": 4 * number})
                for order in permutations(range(3)):
                    hashes.add(checked_hash(reordered(data, order, rename=True)))
        self.assertEqual(len(hashes), 1)

    def test_conjugation_is_reapplied_after_reordering(self):
        data = {
            "gauge_groups": [{"id": name, "algebra": "A2"} for name in "abc"],
            "hypermultiplets": [
                {"representations": {"a": [1, 0], "b": [0, 1]}},
                {"representations": {"b": [1, 0], "c": [0, 1]}},
                {"representations": {"a": [1, 0], "c": [0, 1]}},
            ],
        }
        hashes = set()
        for flipped in product((False, True), repeat=3):
            image = deepcopy(data)
            for hyper in image["hypermultiplets"]:
                for node, flip in zip("abc", flipped):
                    if flip and node in hyper["representations"]:
                        hyper["representations"][node].reverse()
            for order in permutations(range(3)):
                hashes.add(checked_hash(reordered(image, order)))
        self.assertEqual(len(hashes), 1)
        different = deepcopy(data)
        different["hypermultiplets"][0]["representations"]["b"].reverse()
        self.assertNotIn(checked_hash(different), hashes)

    def test_free_hypers_and_multiplicities_are_not_lost(self):
        data = deepcopy(MIXED_PRODUCT)
        data["hypermultiplets"].extend([
            {"representations": {}, "number": 2},
            {"representations": {"a": "adjoint"}, "number": 0},
        ])
        self.assertEqual(checked_hash(data), checked_hash(reordered(data, (1, 0))))
        self.assertNotEqual(checked_hash(data), checked_hash(MIXED_PRODUCT))
        self.assertEqual(len(db._canonical_hashes(db.check_input_data(data))), 2)

    def test_canonical_algebra_order_uses_numeric_rank(self):
        data = {
            "gauge_groups": [{"id": "large", "algebra": "A10"},
                             {"id": "small", "algebra": "A2"}],
            "hypermultiplets": [
                {"representations": {"large": "adjoint"}},
                {"representations": {"small": "adjoint"}},
            ],
        }
        payload = db._canonical_lagrangian_payload(db.check_input_data(data))
        self.assertEqual(payload["gauge_algebras"], ["A2", "A10"])
        self.assertEqual(checked_hash(data), checked_hash(reordered(data, (1, 0))))


MYSQL_DATABASE = os.environ.get("N2_TEST_MYSQL_DATABASE")


@unittest.skipUnless(MYSQL_DATABASE, "set N2_TEST_MYSQL_DATABASE for factor-permutation imports")
class FactorPermutationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "test" not in MYSQL_DATABASE.lower():
            raise RuntimeError("Use a dedicated test database")
        cls.connection = db.connect_database(
            MYSQL_DATABASE, host=os.environ.get("N2_TEST_MYSQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("N2_TEST_MYSQL_PORT", "3306")),
            user=os.environ.get("N2_TEST_MYSQL_USER", "root"),
            password=os.environ.get("N2_TEST_MYSQL_PASSWORD", ""),
            unix_socket=os.environ.get("N2_TEST_MYSQL_UNIX_SOCKET"),
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        db._execute(self.connection, "DELETE FROM theories")

    def count(self, table):
        return db._fetchone(self.connection, f"SELECT COUNT(*) AS n FROM {table}")["n"]

    def snapshot(self, stored):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT factor_order, factor_key, cartan_type FROM gauge_factors "
                "WHERE lagrangian_realization_id = %s ORDER BY factor_order",
                (stored.lagrangian_realization_id,),
            )
            factors = cursor.fetchall()
        return (
            db._fetchone(self.connection, "SELECT * FROM theories WHERE id = %s", (stored.theory_id,)),
            db._fetchone(self.connection, "SELECT * FROM theory_properties WHERE theory_id = %s", (stored.theory_id,)),
            db._fetchone(self.connection, "SELECT * FROM lagrangian_realizations WHERE id = %s", (stored.lagrangian_realization_id,)),
            factors,
        )

    def test_reimports_keep_first_factor_order_ids_sectors_and_indices(self):
        original = reordered(MIXED_PRODUCT, (1, 0), rename=True)
        first = db.store_lagrangian_theory(self.connection, original, name="first ordering")
        db.update_lagrangian_indices(self.connection, first.lagrangian_realization_id,
                                     {"superconformal_index": "1 + t^4", "superconformal_index_order": 8})
        before = self.snapshot(first)
        for data in (MIXED_PRODUCT, reordered(MIXED_PRODUCT, (1, 0))):
            duplicate = db.store_lagrangian_theory(self.connection, data, name="ignored")
            self.assertFalse(duplicate.inserted)
            self.assertEqual(duplicate.theory_id, first.theory_id)
            self.assertEqual(duplicate.lagrangian_realization_id, first.lagrangian_realization_id)
            self.assertEqual(duplicate.name, first.name)
            self.assertEqual(db.find_superconformal_index(self.connection, *_parse_input(data), order=8), "1 + t^4")
            self.assertIsNone(db.find_superconformal_index(self.connection, *_parse_input(data), order=9))
        self.assertEqual(self.snapshot(first), before)
        self.assertEqual(json.loads(before[2]["input_json"]), original)
        self.assertEqual([(row["factor_key"], row["cartan_type"]) for row in before[3]],
                         [(factor["id"], factor["algebra"]) for factor in original["gauge_groups"]])
        self.assertEqual(self.count("theories"), 1)
        self.assertEqual(self.count("lagrangian_realizations"), 1)
        self.assertEqual(self.count("gauge_factors"), 2)

    def legacy_store(self, data):
        payload = db._normalized_lagrangian_payload(db.check_input_data(data))
        digest = hashlib.sha256(db._json_text(payload, canonical=True).encode("utf-8")).hexdigest()
        with patch.object(db, "_canonical_hashes", return_value=(digest,)):
            return db.store_lagrangian_theory(self.connection, data)

    def test_old_reversed_hash_is_reused_without_rewriting_data(self):
        first = self.legacy_store(reordered(MIXED_PRODUCT, (1, 0)))
        self.assertTrue(first.canonical_hash.startswith("e9ffaa51cab6"))
        db.update_lagrangian_indices(self.connection, first.lagrangian_realization_id,
                                     {"superconformal_index": "1 + t^4", "superconformal_index_order": 8})
        before = self.snapshot(first)
        duplicate = db.store_lagrangian_theory(self.connection, MIXED_PRODUCT)
        self.assertFalse(duplicate.inserted)
        self.assertEqual(duplicate.theory_id, first.theory_id)
        self.assertTrue(duplicate.canonical_hash.startswith("3c94753090a8"))
        self.assertEqual(db.find_superconformal_index(self.connection, *_parse_input(MIXED_PRODUCT), order=8), "1 + t^4")
        self.assertEqual(self.snapshot(first), before)
        self.assertEqual(self.count("theories"), 1)

    def test_existing_order_duplicates_do_not_create_another_row(self):
        first = self.legacy_store(reordered(MIXED_PRODUCT, (1, 0)))
        second = self.legacy_store(MIXED_PRODUCT)
        for stored, order, value in ((first, 4, "1"), (second, 8, "1 + t^4")):
            db.update_lagrangian_indices(self.connection, stored.lagrangian_realization_id,
                                         {"superconformal_index": value, "superconformal_index_order": order})
        duplicate = db.store_lagrangian_theory(self.connection, MIXED_PRODUCT)
        self.assertFalse(duplicate.inserted)
        self.assertEqual(duplicate.theory_id, first.theory_id)
        self.assertEqual(self.count("theories"), 2)
        self.assertEqual(db.find_superconformal_index(self.connection, *_parse_input(MIXED_PRODUCT), order=6), "1 + t^4")

    def test_repeated_factor_permutations_and_different_quivers(self):
        chain = su2_quiver(4, [(0, 1), (1, 2), (2, 3)])
        results = [db.store_lagrangian_theory(self.connection, reordered(chain, order))
                   for order in permutations(range(4))]
        self.assertEqual(sum(row.inserted for row in results), 1)
        self.assertEqual(len({row.theory_id for row in results}), 1)
        disconnected = su2_quiver(4, [(0, 1), (2, 3)])
        self.assertTrue(db.store_lagrangian_theory(self.connection, disconnected).inserted)
        self.assertEqual(self.count("theories"), 2)


if __name__ == "__main__":
    unittest.main()
