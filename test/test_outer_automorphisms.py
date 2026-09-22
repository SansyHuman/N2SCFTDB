from copy import deepcopy
import hashlib
from itertools import permutations, product
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from anomalies.lie_algebra import diagram_automorphisms, get_lie_algebra
from common import n2_theory_db as db
from common.n2_theory_iter import enumerate_simple_theory_candidates
from index.n2_theory_index import _parse_input


D4_LABELS = ((1, 0, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))


def simple(algebra, *matter):
    return {"algebra": algebra, "hypermultiplets": [
        {"dynkin_labels": list(labels), "number": number, "kind": kind}
        for labels, number, kind in matter
    ]}


def triality_examples():
    return [simple("D4", (labels, 6, "full")) for labels in D4_LABELS]


def mixed_triality_examples():
    return [simple("D4", (left, 3, "full"), (right, 3, "full"))
            for left, right in permutations(D4_LABELS, 2)]


def bifundamentals(*orientations):
    return {
        "gauge_groups": [{"id": name, "algebra": "A2"} for name in ("a", "b")],
        "hypermultiplets": [
            {"representations": {"a": [1, 0], "b": list(right)}}
            for right in orientations
        ],
    }


def checked_hash(data):
    return db._canonical_hash(db.check_input_data(data))


class DiagramAutomorphismTests(unittest.TestCase):
    def test_group_orders_and_preservation_of_cartan_matrices(self):
        cases = {
            "A1": 1, "A2": 2, "A5": 2, "B2": 1, "B4": 1,
            "C2": 1, "C4": 1, "D4": 6, "D5": 2, "D6": 2,
            "D10": 2, "E6": 2, "E7": 1, "E8": 1, "F4": 1, "G2": 1,
        }
        for cartan_type, order in cases.items():
            with self.subTest(cartan_type=cartan_type):
                algebra = get_lie_algebra(cartan_type)
                group = diagram_automorphisms(algebra)
                self.assertEqual(len(group), order)
                self.assertEqual(len(set(group)), order)
                self.assertIn(tuple(range(algebra.rank)), group)
                matrix = algebra.sage_cartan_type.cartan_matrix()
                for permutation in group:
                    for i, j in product(range(algebra.rank), repeat=2):
                        self.assertEqual(matrix[permutation[i], permutation[j]], matrix[i, j])
                    for other in group:
                        self.assertIn(tuple(permutation[i] for i in other), group)

    def test_d4_and_e6_use_sage_node_numbering(self):
        expected = set()
        for outer in permutations((0, 2, 3)):
            expected.add((outer[0], 1, outer[1], outer[2]))
        self.assertEqual(set(diagram_automorphisms("D4")), expected)
        self.assertEqual(diagram_automorphisms("E6"), (
            (0, 1, 2, 3, 4, 5), (5, 1, 4, 3, 2, 0),
        ))


class OuterAutomorphismHashTests(unittest.TestCase):
    def test_triality_merges_each_orbit_but_preserves_different_matter(self):
        pure = {checked_hash(data) for data in triality_examples()}
        mixed = {checked_hash(data) for data in mixed_triality_examples()}
        self.assertEqual(len(pure), 1)
        self.assertEqual(len(mixed), 1)
        self.assertTrue(pure.isdisjoint(mixed))

    def test_d4_enumeration_retains_mixed_irreps_and_has_eight_orbits(self):
        candidates = list(enumerate_simple_theory_candidates("D4"))
        self.assertEqual(len(candidates), 29)
        self.assertEqual(len({checked_hash(data) for data in candidates}), 8)

    def test_general_dynkin_labels_transform_with_the_whole_configuration(self):
        # Non-fundamental representations ensure this is not an 8v/8s/8c alias fix.
        for algebra, rows in (
            ("D4", ((2, 0, 1, 0), (0, 1, 0, 2))),
            ("D6", ((1, 0, 0, 0, 2, 0), (0, 1, 0, 0, 0, 1))),
            ("A3", ((1, 1, 0), (0, 0, 2))),
            ("E6", ((1, 0, 0, 0, 0, 0), (0, 0, 1, 0, 0, 0))),
        ):
            hashes = set()
            for permutation in diagram_automorphisms(algebra):
                data = simple(algebra, *(
                    (tuple(row[i] for i in permutation), number, "full")
                    for number, row in enumerate(rows, 1)
                ))
                hashes.add(checked_hash(data))
            with self.subTest(algebra=algebra):
                self.assertEqual(len(hashes), 1)

    def test_d6_spinor_exchange_preserves_half_hyper_normalization(self):
        hashes = set()
        for last in ((1, 0), (0, 1)):
            for kind, number in (("full", 1), ("half", 2)):
                data = simple("D6", ((1, 0, 0, 0, 0, 0), 6, "full"),
                              ((0, 0, 0, 0) + last, number, kind))
                checked = db.check_input_data(data)
                self.assertTrue(checked["lagrangian_scft_candidate"])
                hashes.add(db._canonical_hash(checked))
        self.assertEqual(len(hashes), 1)

    def test_product_factor_action_preserves_relative_orientations(self):
        same = bifundamentals((1, 0), (1, 0))
        transformed = bifundamentals((0, 1), (0, 1))
        mixed = bifundamentals((1, 0), (0, 1))
        for data in (same, transformed, mixed):
            self.assertTrue(db.check_input_data(data)["lagrangian_scft_candidate"])
        self.assertEqual(checked_hash(same), checked_hash(transformed))
        self.assertNotEqual(checked_hash(same), checked_hash(mixed))

    def test_triality_acts_on_bifundamental_and_single_factor_matter_together(self):
        hashes = set()
        for labels in D4_LABELS:
            data = {
                "gauge_groups": [{"id": "d", "algebra": "D4"},
                                 {"id": "a", "algebra": "A1"}],
                "hypermultiplets": [
                    {"representations": {"d": list(labels), "a": [1]}, "kind": "half"},
                    {"representations": {"d": list(labels)}, "number": 5},
                ],
            }
            self.assertTrue(db.check_input_data(data)["lagrangian_scft_candidate"])
            hashes.add(checked_hash(data))
        self.assertEqual(len(hashes), 1)
        data["hypermultiplets"][1]["representations"]["d"] = list(D4_LABELS[0])
        self.assertNotIn(checked_hash(data), hashes)

    def test_normalization_is_idempotent_and_does_not_mutate_input(self):
        data = simple("D4", (D4_LABELS[2], 2, "full"),
                      (D4_LABELS[2], 4, "full"), (D4_LABELS[1], 0, "full"))
        original = deepcopy(data)
        checked = db.check_input_data(data)
        payload = db._canonical_lagrangian_payload(checked)
        self.assertEqual(data, original)
        self.assertEqual(payload["hypermultiplets"], [
            {"kind": "full", "dynkin_labels": [list(D4_LABELS[0])], "number": 6},
        ])
        canonical_input = simple("D4", *(
            (row["dynkin_labels"][0], row["number"], row["kind"])
            for row in payload["hypermultiplets"]
        ))
        self.assertEqual(checked_hash(data), checked_hash(canonical_input))
        data["hypermultiplets"].reverse()
        self.assertEqual(checked_hash(data), checked_hash(original))

    def test_legacy_triality_hashes_are_recognized_from_every_input(self):
        expected = {"ab4d6dfc66cc", "c5be49c125f5", "5a24b14e2aaa"}
        for data in triality_examples():
            hashes = db._canonical_hashes(db.check_input_data(data))
            self.assertEqual({h[:12] for h in hashes}, expected)
            self.assertEqual(hashes[0][:12], "ab4d6dfc66cc")

    def test_legacy_duplicate_import_is_read_only_and_returns_current_hash(self):
        winner = {"theory_id": 7, "realization_id": 8,
                  "gauge_group": "Spin(8)", "name": "original spinor"}
        connection = MagicMock()
        with patch.object(db, "_fetchone", return_value=winner) as read:
            stored = db.store_lagrangian_theory(connection, triality_examples()[0],
                                                initialize_schema=False)
        self.assertFalse(stored.inserted)
        self.assertEqual((stored.theory_id, stored.lagrangian_realization_id), (7, 8))
        sql, parameters = read.call_args.args[1:]
        self.assertIn("ORDER BY lr.id", sql)
        self.assertEqual({h[:12] for h in parameters},
                         {"ab4d6dfc66cc", "c5be49c125f5", "5a24b14e2aaa"})
        connection.begin.assert_not_called()
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()


MYSQL_DATABASE = os.environ.get("N2_TEST_MYSQL_DATABASE")


@unittest.skipUnless(MYSQL_DATABASE, "set N2_TEST_MYSQL_DATABASE for outer-automorphism imports")
class OuterAutomorphismDatabaseTests(unittest.TestCase):
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

    def test_import_all_d4_candidates_stores_eight_theories(self):
        results = [db.store_lagrangian_theory(self.connection, data)
                   for data in enumerate_simple_theory_candidates("D4")]
        self.assertEqual(sum(result.inserted for result in results), 8)
        self.assertEqual(self.count("theories"), 8)
        self.assertEqual(self.count("lagrangian_realizations"), 8)

    def test_orbit_reimports_keep_original_data_and_indices(self):
        original = triality_examples()[2]
        first = db.store_lagrangian_theory(self.connection, original, name="original")
        db.update_lagrangian_indices(self.connection, first.lagrangian_realization_id,
                                     {"superconformal_index": "1 + t^4", "superconformal_index_order": 8})
        before = db._fetchone(self.connection, "SELECT * FROM theory_properties WHERE theory_id = %s", (first.theory_id,))
        for data in triality_examples():
            duplicate = db.store_lagrangian_theory(self.connection, data, name="ignored")
            self.assertFalse(duplicate.inserted)
            self.assertEqual(duplicate.lagrangian_realization_id, first.lagrangian_realization_id)
            self.assertEqual(duplicate.theory_id, first.theory_id)
            self.assertEqual(duplicate.name, "original")
            self.assertEqual(db.find_superconformal_index(self.connection, *_parse_input(data), order=8), "1 + t^4")
            self.assertIsNone(db.find_superconformal_index(self.connection, *_parse_input(data), order=9))
        self.assertEqual(db._fetchone(self.connection, "SELECT * FROM theory_properties WHERE theory_id = %s", (first.theory_id,)), before)
        row = db._fetchone(self.connection, "SELECT input_json FROM lagrangian_realizations WHERE id = %s", (first.lagrangian_realization_id,))
        self.assertEqual(json.loads(row["input_json"]), original)
        self.assertEqual(self.count("theories"), 1)
        self.assertTrue(db.store_lagrangian_theory(self.connection, mixed_triality_examples()[0]).inserted)

    def legacy_store(self, data):
        checked = db.check_input_data(data)
        text = db._json_text(db._normalized_lagrangian_payload(checked), canonical=True)
        legacy_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Recreate an import made by the previous canonicalizer.
        with patch.object(db, "_canonical_hashes", return_value=(legacy_hash,)):
            return db.store_lagrangian_theory(self.connection, data)

    def test_recognizes_legacy_noncanonical_row_without_rewriting_it(self):
        first = self.legacy_store(triality_examples()[2])
        self.assertTrue(first.canonical_hash.startswith("5a24b14e2aaa"))
        db.update_lagrangian_indices(self.connection, first.lagrangian_realization_id,
                                     {"superconformal_index": "1 + t^4", "superconformal_index_order": 8})
        for data in triality_examples():
            stored = db.store_lagrangian_theory(self.connection, data)
            self.assertFalse(stored.inserted)
            self.assertEqual(stored.theory_id, first.theory_id)
            self.assertTrue(stored.canonical_hash.startswith("ab4d6dfc66cc"))
            self.assertEqual(db.find_superconformal_index(self.connection, *_parse_input(data), order=8), "1 + t^4")
        row = db._fetchone(self.connection, "SELECT canonical_hash FROM lagrangian_realizations WHERE id = %s", (first.lagrangian_realization_id,))
        self.assertEqual(row["canonical_hash"], first.canonical_hash)
        self.assertEqual(self.count("theories"), 1)

    def test_existing_legacy_duplicates_do_not_create_more_rows(self):
        first = self.legacy_store(triality_examples()[2])
        second = self.legacy_store(triality_examples()[1])
        for stored, order, index in ((first, 4, "1 + t^4"), (second, 8, "1 + t^4 + t^6")):
            db.update_lagrangian_indices(self.connection, stored.lagrangian_realization_id,
                                         {"superconformal_index": index, "superconformal_index_order": order})
        data = triality_examples()[0]
        stored = db.store_lagrangian_theory(self.connection, data)
        self.assertFalse(stored.inserted)
        self.assertEqual(stored.theory_id, first.theory_id)
        self.assertEqual(self.count("theories"), 2)
        self.assertEqual(db.find_superconformal_index(self.connection, *_parse_input(data), order=6), "1 + t^4 + t^6")
        with self.assertRaisesRegex(ValueError, "already attached"):
            db.store_lagrangian_theory(self.connection, data, theory_id=second.theory_id)

    def test_product_orientations_and_d6_half_hypers(self):
        same = db.store_lagrangian_theory(self.connection, bifundamentals((1, 0), (1, 0)))
        duplicate = db.store_lagrangian_theory(self.connection, bifundamentals((0, 1), (0, 1)))
        different = db.store_lagrangian_theory(self.connection, bifundamentals((1, 0), (0, 1)))
        self.assertEqual(duplicate.theory_id, same.theory_id)
        self.assertFalse(duplicate.inserted)
        self.assertNotEqual(different.theory_id, same.theory_id)
        ids = set()
        for last in ((1, 0), (0, 1)):
            for kind, number in (("full", 1), ("half", 2)):
                data = simple("D6", ((1, 0, 0, 0, 0, 0), 6, "full"),
                              ((0, 0, 0, 0) + last, number, kind))
                ids.add(db.store_lagrangian_theory(self.connection, data).theory_id)
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.count("theories"), 3)


if __name__ == "__main__":
    unittest.main()
