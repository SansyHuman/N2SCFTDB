"""Disconnected-sector projection, exact products and optional MySQL reuse."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from anomalies.check_n2_anomalies import HyperData, ProductHyperData
from common import n2_theory_db as db
from common import n2_theory_db_indices as worker
from index import n2_theory_index as idx


SU2 = {"algebra": "A1", "hypermultiplets": [{"representation": "fundamental", "number": 4}]}
SU3 = {"algebra": "A2", "hypermultiplets": [{"representation": "fundamental", "number": 6}]}
BIFUNDAMENTAL = {
    "gauge_groups": [{"id": "a", "algebra": "A1"}, {"id": "b", "algebra": "A1"}],
    "hypermultiplets": [{"representations": {"a": "fundamental", "b": "fundamental"}, "number": 2}],
}


def product_of(*theories):
    factors, hypers = [], []
    for position, data in enumerate(theories):
        parsed_factors, parsed_hypers = idx._parse_input(data)
        names = {f.factor_id: f"s{position}_{f.factor_id}" for f in parsed_factors}
        factors.extend({"id": names[f.factor_id], "algebra": f.algebra.cartan_type} for f in parsed_factors)
        for hyper in parsed_hypers:
            representations = (hyper.representations if isinstance(hyper, ProductHyperData)
                               else {parsed_factors[0].factor_id: hyper.representation})
            hypers.append({"representations": {names[k]: list(rep.labels) for k, rep in representations.items()},
                           "number": hyper.number, "kind": hyper.kind})
    return {"gauge_groups": factors, "hypermultiplets": hypers}


class SectorPartitionTests(unittest.TestCase):
    def test_transitive_connections_zero_multiplicity_and_free_hypers(self):
        data = {
            "gauge_groups": [{"id": name, "algebra": "A1"} for name in "abcd"],
            "hypermultiplets": [
                {"representations": {"a": "fundamental", "b": "fundamental"}},
                {"representations": {"b": "fundamental", "c": "fundamental"}},
                {"representations": {"c": "fundamental", "d": "fundamental"}, "number": 0},
                {"representations": {"d": "fundamental"}, "number": 4},
                {"representations": {}, "number": 2},
            ],
        }
        factors, hypers = idx._parse_input(data)
        original = [asdict(h) for h in hypers]
        sectors = idx.split_disconnected_sectors(factors, hypers)
        self.assertEqual([[f.factor_id for f in fs] for fs, _ in sectors], [list("abc"), ["d"], []])
        self.assertEqual([len(hs) for _, hs in sectors], [2, 1, 1])
        self.assertEqual([asdict(h) for h in hypers], original)
        for hyper in sectors[0][1]:
            self.assertEqual(set(hyper.representations), set("abc"))
            self.assertEqual(set(hyper.beta_contributions), set("abc"))
        self.assertIsInstance(sectors[1][1][0], HyperData)
        free = sectors[2][1][0]
        self.assertEqual((free.representations, free.beta_contributions, free.dimension, free.number), ({}, {}, 1, 2))

    def test_half_trifundamental_connects_all_three_factors(self):
        data = {"gauge_groups": [{"id": name, "algebra": "A1"} for name in "abcd"],
                "hypermultiplets": [{"representations": dict.fromkeys("abc", "fundamental"), "kind": "half"}]}
        sectors = idx.split_disconnected_sectors(*idx._parse_input(data))
        self.assertEqual([[f.factor_id for f in fs] for fs, _ in sectors], [list("abc"), ["d"]])
        self.assertEqual(sectors[0][1][0].kind, "half")
        self.assertEqual(sectors[1][1], [])  # Retain the isolated vector sector.

    def test_simple_free_hyper_is_normalized_without_a_gauge_factor(self):
        data = {"algebra": "A1", "hypermultiplets": [{"representation": "singlet", "number": 3}]}
        sectors = idx.split_disconnected_sectors(*idx._parse_input(data))
        self.assertEqual([len(fs) for fs, _ in sectors], [1, 0])
        self.assertEqual(idx._matter_character_multiplicities(*sectors[1]), {(): 6})


class SectorReuseTests(unittest.TestCase):
    def test_higher_order_stored_indices_multiply_exactly_and_truncate(self):
        left = idx.parse_index_polynomial("1 + t^4*y/u^2/3 + 7*t^10")
        right = idx.parse_index_polynomial("1 - 2*t^4/u + 11*t^12")
        connection = object()
        with patch.object(db, "find_superconformal_index", side_effect=[str(left), str(right)]) as lookup, \
             patch.object(idx, "_calculate_sector_index", side_effect=AssertionError("unexpected FORM")):
            actual = idx.calculate_index(product_of(SU2, SU3), 8, theory_db_connection=connection)
        self.assertEqual(actual, idx.parse_index_polynomial(
            "1 + t^4*y/u^2/3 - 2*t^4/u - 2*t^8*y/u^3/3"))
        self.assertEqual(lookup.call_count, 2)
        self.assertTrue(all(call.args[0] is connection and call.kwargs == {"order": 8}
                            for call in lookup.call_args_list))
        self.assertIs(actual.parent(), idx.INDEX_POLYNOMIAL_RING)

    def test_partial_hit_calculates_only_missing_sector_and_forwards_options(self):
        options = dict(char_cache_database_path="/unused/characters.sqlite", form_cache_database_path="/unused/form.sqlite",
                       lie_executable="custom-lie", form_executable="custom-form",
                       tform_executable="custom-tform", form_threads=4, timeout=19, processes=1)
        with patch.object(db, "find_superconformal_index", side_effect=["1 + t^4", None]), \
             patch.object(idx, "_calculate_sector_index", return_value=idx.parse_index_polynomial("1 + t^6")) as calculate:
            actual = idx.calculate_index(product_of(SU2, SU3), 8, theory_db_connection=object(), **options)
        calculate.assert_called_once()
        self.assertEqual(calculate.call_args.args[0][0].algebra.cartan_type, "A2")
        self.assertEqual(calculate.call_args.kwargs, options)
        self.assertEqual(actual, idx.parse_index_polynomial("1 + t^4 + t^6"))

    def test_repeated_sectors_calculate_once_without_a_database(self):
        with patch.object(db, "find_superconformal_index", side_effect=AssertionError("unexpected DB")), \
             patch.object(idx, "_calculate_sector_index", return_value=idx.parse_index_polynomial("1 + t^4")) as calculate:
            actual = idx.calculate_index(product_of(SU2, SU2, SU2), 8)
        calculate.assert_called_once()
        self.assertEqual(actual, idx.parse_index_polynomial("1 + 3*t^4 + 3*t^8"))

    def test_invalid_stored_polynomials_fall_back_to_calculation(self):
        for text in ("", "not_an_index", "1 + t^-1"):
            with self.subTest(text=text), patch.object(db, "find_superconformal_index", return_value=text), \
                 patch.object(idx, "_calculate_sector_index", return_value=idx.INDEX_POLYNOMIAL_RING.one()) as calculate:
                self.assertEqual(idx.calculate_index(SU2, 4, theory_db_connection=object()), 1)
                calculate.assert_called_once()

    def test_connection_errors_are_not_silently_treated_as_cache_misses(self):
        with patch.object(db, "find_superconformal_index", side_effect=db.pymysql.OperationalError(2013, "lost")), \
             patch.object(idx, "_calculate_sector_index") as calculate, self.assertRaises(db.pymysql.OperationalError):
            idx.calculate_index(SU2, 4, theory_db_connection=object())
        calculate.assert_not_called()

    def test_low_order_needs_neither_database_nor_backend(self):
        with patch.object(db, "find_superconformal_index", side_effect=AssertionError("unexpected DB")), \
             patch.object(idx, "_calculate_sector_index", side_effect=AssertionError("unexpected FORM")):
            for order in (0, 1):
                self.assertEqual(idx.calculate_index(product_of(SU2, SU3), order, theory_db_connection=object()), 1)


@unittest.skipUnless(shutil.which("form") and shutil.which("lie"), "FORM and LiE are required")
class SectorProjectionTests(unittest.TestCase):
    def test_split_indices_match_unsplit_form_projection_with_free_hypers(self):
        cases = [product_of(SU2, SU3), product_of(BIFUNDAMENTAL, SU2)]
        cases[0]["hypermultiplets"].append({"representations": {}, "number": 2})
        cases.append({"algebra": "A1", "hypermultiplets": [{"representation": "singlet", "number": 1}]})
        with tempfile.TemporaryDirectory() as directory:
            for data in cases:
                with self.subTest(data=data):
                    factors, hypers = idx._parse_input(data)
                    options = dict(char_cache_database_path=Path(directory) / "characters.db", processes=1)
                    expected = idx._calculate_sector_index(factors, hypers, 8, **options)
                    actual = idx.calculate_index_internal(factors, hypers, 8, **options)
                    self.assertEqual(actual, expected)
                    self.assertTrue(all(powers[0] <= 8 for powers in actual.dict()))


MYSQL_DATABASE = os.environ.get("N2_TEST_MYSQL_DATABASE")


@unittest.skipUnless(MYSQL_DATABASE, "set N2_TEST_MYSQL_DATABASE for sector reuse tests")
class SectorDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "test" not in MYSQL_DATABASE.lower():
            raise RuntimeError("Use a dedicated test database")
        cls.connection = db.connect_database(
            MYSQL_DATABASE, host=os.environ.get("N2_TEST_MYSQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("N2_TEST_MYSQL_PORT", "3306")),
            user=os.environ.get("N2_TEST_MYSQL_USER", "root"), password=os.environ.get("N2_TEST_MYSQL_PASSWORD", ""),
            unix_socket=os.environ.get("N2_TEST_MYSQL_UNIX_SOCKET"),
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        db._execute(self.connection, "DELETE FROM theories")

    def store(self, data, polynomial=None, order=None):
        stored = db.store_lagrangian_theory(self.connection, data)
        if polynomial is not None:
            db.update_lagrangian_indices(self.connection, stored.lagrangian_realization_id,
                                         {"superconformal_index": polynomial, "superconformal_index_order": order})
        return stored

    def test_lookup_matches_simple_product_conjugate_and_half_hyper_conventions(self):
        self.store(SU2, "1 + 28*t^4/u^2 + t^4*u^4", 8)
        self.store(SU3, "1 + 36*t^4/u^2 + t^4*u^4", 8)
        for data in (SU2, product_of(SU2), {"algebra": "A1", "hypermultiplets": [
                {"representation": "fundamental", "number": 8, "kind": "half"}]}):
            self.assertEqual(db.find_superconformal_index(self.connection, *idx._parse_input(data), order=8),
                             "1 + 28*t^4/u^2 + t^4*u^4")
        conjugate = {"algebra": "A2", "hypermultiplets": [{"dynkin_labels": [0, 1], "number": 6}]}
        self.assertEqual(db.find_superconformal_index(self.connection, *idx._parse_input(conjugate), order=8),
                         "1 + 36*t^4/u^2 + t^4*u^4")

    def test_lookup_requires_recorded_precision_and_a_nonnull_string(self):
        factors, hypers = idx._parse_input(SU2)
        lookup = lambda order: db.find_superconformal_index(self.connection, factors, hypers, order=order)
        self.assertIsNone(lookup(6))  # Absent theory.
        stored = self.store(SU2)
        self.assertIsNone(lookup(6))  # Present theory, absent index.
        for text, cutoff, expected in (("1 + t^20", 4, None), ("1 + t^20", None, None),
                                       (None, 8, None), ("1", 6, "1"), ("1", 8, "1")):
            with self.subTest(text=text, cutoff=cutoff):
                db._execute(self.connection, "UPDATE theory_properties SET superconformal_index_json = %s, "
                            "superconformal_index_order = %s WHERE theory_id = %s",
                            (json.dumps(text), cutoff, stored.theory_id))
                self.assertEqual(lookup(6), expected)

    def test_worker_reuses_product_and_simple_sector_indices_without_writing_sector_rows(self):
        self.store(BIFUNDAMENTAL, "1 + 2*t^4*u^4 + 10*t^4/u^2 + 7*t^10", 10)
        self.store(SU2, "1 + 28*t^4/u^2 + t^4*u^4 + 5*t^12", 12)
        data = product_of(BIFUNDAMENTAL, SU2)
        original = self.store(data)
        selected = next(job for job in db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0)
                        if job["theory_id"] == original.theory_id)
        selected["needed_fields"] = ["superconformal_index"]
        def sector_rows():
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT * FROM theory_properties WHERE theory_id <> %s ORDER BY theory_id", (original.theory_id,))
                return cursor.fetchall()
        before = sector_rows()
        with patch.object(idx, "_calculate_sector_index", side_effect=AssertionError("unexpected FORM")):
            result = worker.calculate_index_job(self.connection, selected, order=8, max_dimension=0)
        self.assertEqual(result["errors"], {})
        self.assertEqual(result["updated_fields"], ["superconformal_index", "superconformal_index_order"])
        self.assertEqual(sector_rows(), before)
        row = db._fetchone(self.connection, "SELECT superconformal_index_json, superconformal_index_order "
                          "FROM theory_properties WHERE theory_id = %s", (original.theory_id,))
        expected = idx.parse_index_polynomial("1 + 3*t^4*u^4 + 38*t^4/u^2 + 2*t^8*u^8 + 66*t^8*u^2 + 280*t^8/u^4")
        self.assertEqual(idx.parse_index_polynomial(json.loads(row["superconformal_index_json"])), expected)
        self.assertEqual(row["superconformal_index_order"], 8)

    def test_lookup_preserves_callers_open_transaction(self):
        self.store(SU2, "1", 8)
        self.connection.begin()
        try:
            db._execute(self.connection, "UPDATE theories SET name = 'uncommitted fixture'")
            with patch.object(self.connection, "commit", side_effect=AssertionError("unexpected commit")), \
                 patch.object(self.connection, "rollback", side_effect=AssertionError("unexpected rollback")), \
                 patch.object(self.connection, "close", side_effect=AssertionError("unexpected close")):
                self.assertEqual(db.find_superconformal_index(self.connection, *idx._parse_input(SU2), order=6), "1")
        finally:
            self.connection.rollback()
        self.assertNotEqual(db._fetchone(self.connection, "SELECT name FROM theories")["name"], "uncommitted fixture")


if __name__ == "__main__":
    unittest.main()
