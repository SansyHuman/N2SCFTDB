from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from common import n2_theory_db as database
from common import n2_theory_properties as theory_properties


E6_SCFT = {
    "algebra": "E6",
    "hypermultiplets": [
        {"representation": "fundamental", "number": 4}
    ],
}

A1_PRODUCT_SCFT = {
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
        }
    ],
}


def _pseudoreal_matter(*blocks, product=False):
    if product:
        return {
            "gauge_groups": [
                {"id": key, "algebra": "A1"} for key in ("a", "b", "c")
            ],
            "hypermultiplets": [
                {
                    "representations": dict.fromkeys(("a", "b", "c"), "fundamental"),
                    "kind": kind, "number": number,
                }
                for kind, number in blocks
            ],
        }
    return {
        "algebra": "A1",
        "hypermultiplets": [
            {"representation": "fundamental", "kind": kind, "number": number}
            for kind, number in blocks
        ],
    }


class _RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.lastrowid = None
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, statement, parameters=()):
        normalized = " ".join(statement.split())
        self.connection.statements.append((normalized, parameters))
        if normalized.startswith("SELECT"):
            self._row = (
                self.connection.select_rows.pop(0)
                if self.connection.select_rows
                else None
            )
        if normalized.startswith("INSERT"):
            self.lastrowid = self.connection.next_id
            self.connection.next_id += 1
        return 1

    def fetchone(self):
        return self._row


class _RecordingConnection:
    def __init__(self, select_rows=None):
        self.statements = []
        self.select_rows = list(select_rows or [])
        self.next_id = 1

    def cursor(self, cursor=None):
        return _RecordingCursor(self)


class TheoryDatabaseUnitTests(unittest.TestCase):
    def setUp(self):
        index_patcher = patch(
            "common.n2_theory_properties.calculate_index_internal",
            return_value="mock_index",
        )
        index_patcher.start()
        self.addCleanup(index_patcher.stop)
        coulomb_index_patcher = patch(
            "common.n2_theory_properties.calculate_lagrangian_coulomb_branch_index",
            return_value="mock_coulomb_index",
        )
        coulomb_index_patcher.start()
        self.addCleanup(coulomb_index_patcher.stop)

    def test_simple_conjugate_hashes_prefer_lower_dynkin_nodes(self):
        cases = (
            ("A2", (1, 0), (0, 1)),
            ("A4", (1, 0, 0, 0), (0, 0, 0, 1)),
            ("D5", (0, 0, 0, 1, 0), (0, 0, 0, 0, 1)),
            ("E6", (1, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 1)),
        )
        for algebra, preferred, conjugate in cases:
            hashes = set()
            for labels in (preferred, conjugate):
                with self.subTest(algebra=algebra, labels=labels):
                    checked = database.check_input_data({
                        "algebra": algebra,
                        "hypermultiplets": [{"dynkin_labels": labels}],
                    })
                    payload = database._canonical_lagrangian_payload(checked)
                    self.assertEqual(
                        payload["hypermultiplets"][0]["dynkin_labels"],
                        [list(preferred)],
                    )
                    hashes.add(database._canonical_hash(checked))
            self.assertEqual(len(hashes), 1)

    def test_product_hashes_identify_whole_factor_conjugation(self):
        fundamental, antifundamental = (1, 0), (0, 1)
        hashes_by_representation = {}
        for left, right, expected in (
            (fundamental, fundamental, (fundamental, fundamental)),
            (antifundamental, antifundamental, (fundamental, fundamental)),
            (fundamental, antifundamental, (fundamental, fundamental)),
            (antifundamental, fundamental, (fundamental, fundamental)),
        ):
            with self.subTest(left=left, right=right):
                checked = database.check_input_data({
                    "gauge_groups": [
                        {"id": "left", "algebra": "A2"},
                        {"id": "right", "algebra": "A2"},
                    ],
                    "hypermultiplets": [{
                        "representations": {"left": list(left), "right": list(right)},
                        "number": 2,
                    }],
                })
                payload = database._canonical_lagrangian_payload(checked)
                self.assertEqual(
                    payload["hypermultiplets"][0]["dynkin_labels"],
                    [list(labels) for labels in expected],
                )
                hashes_by_representation.setdefault(expected, set()).add(
                    database._canonical_hash(checked)
                )
        self.assertEqual(len(hashes_by_representation), 1)
        self.assertTrue(all(len(hashes) == 1 for hashes in hashes_by_representation.values()))
        self.assertEqual(len(set().union(*hashes_by_representation.values())), 1)

    def test_schema_uses_mysql_types_and_innodb(self):
        self.assertIn("AUTO_INCREMENT", database.SCHEMA_SQL)
        self.assertIn("ENGINE=InnoDB", database.SCHEMA_SQL)
        self.assertIn("input_json JSON NOT NULL", database.SCHEMA_SQL)
        self.assertIn(
            "central_charge_a_decimal DECIMAL(65, 30)",
            database.SCHEMA_SQL,
        )
        self.assertIn(
            "central_charge_c_decimal DECIMAL(65, 30)",
            database.SCHEMA_SQL,
        )
        self.assertIn("GENERATED ALWAYS AS", database.SCHEMA_SQL)
        self.assertIn(
            "idx_theory_properties_central_charge_a",
            database.SCHEMA_SQL,
        )
        self.assertIn(
            "coulomb_branch_index_json JSON NULL", database.SCHEMA_SQL
        )
        self.assertIn(
            "coulomb_branch_spectrum_json JSON NULL", database.SCHEMA_SQL
        )
        self.assertIn("superconformal_index_json JSON NULL", database.SCHEMA_SQL)
        self.assertNotIn("superconformal_indices_json", database.SCHEMA_SQL)
        self.assertNotIn("AUTOINCREMENT", database.SCHEMA_SQL)
        self.assertNotIn("CREATE INDEX IF NOT EXISTS", database.SCHEMA_SQL)
        self.assertNotIn("full_hypermultiplets", database.SCHEMA_SQL)
        self.assertNotIn("half_hypermultiplets", database.SCHEMA_SQL)
        self.assertIn("half_hyper_units BIGINT UNSIGNED NOT NULL", database.SCHEMA_SQL)

    def test_pseudoreal_hash_ignores_full_half_split(self):
        cases = (
            (False, 4, [("full", 2), ("half", 3), ("half", 1)]),
            (True, 1, [("half", 1), ("half", 1)]),
        )
        for product, number, split in cases:
            hashes = set()
            for blocks in (
                [("full", number)], [("half", 2 * number)], split,
                list(reversed(split)) + [("full", 0)],
            ):
                with self.subTest(product=product, blocks=blocks):
                    data = _pseudoreal_matter(*blocks, product=product)
                    original = deepcopy(data)
                    checked = database.check_input_data(data)
                    self.assertTrue(checked["lagrangian_scft_candidate"])
                    payload = database._canonical_lagrangian_payload(checked)
                    self.assertEqual(payload["hypermultiplets"], [{
                        "kind": "full", "number": number,
                        "dynkin_labels": [[1]] * (3 if product else 1),
                    }])
                    hashes.add(database._canonical_hash(checked))
                    self.assertEqual(data, original)
            self.assertEqual(len(hashes), 1)

    def test_odd_half_hypers_are_paired_within_each_representation(self):
        hashes = set()
        for fundamental in (
            [{"dynkin_labels": [1, 0, 0], "kind": "half", "number": 11}],
            [
                {"dynkin_labels": [1, 0, 0], "kind": "full", "number": 5},
                {"dynkin_labels": [1, 0, 0], "kind": "half", "number": 1},
            ],
        ):
            checked = database.check_input_data({
                "algebra": "C3",
                "hypermultiplets": fundamental + [
                    {"dynkin_labels": [0, 0, 1], "kind": "half", "number": 1}
                ],
            })
            self.assertTrue(checked["lagrangian_scft_candidate"])
            self.assertEqual(
                database._canonical_lagrangian_payload(checked)["hypermultiplets"],
                [
                    {"kind": "full", "dynkin_labels": [[1, 0, 0]], "number": 5},
                    {"kind": "half", "dynkin_labels": [[0, 0, 1]], "number": 1},
                    {"kind": "half", "dynkin_labels": [[1, 0, 0]], "number": 1},
                ],
            )
            hashes.add(database._canonical_hash(checked))
        self.assertEqual(len(hashes), 1)

    def test_real_product_representation_remains_full(self):
        checked = database.check_input_data(A1_PRODUCT_SCFT)
        self.assertEqual(checked["hypermultiplets"][0].reality, "real")
        self.assertEqual(
            database._canonical_lagrangian_payload(checked)["hypermultiplets"],
            [{"kind": "full", "dynkin_labels": [[1], [1]], "number": 2}],
        )
        invalid = deepcopy(A1_PRODUCT_SCFT)
        invalid["hypermultiplets"][0].update(kind="half", number=4)
        with self.assertRaisesRegex(database.TheoryCheckError, "pseudoreal"):
            database._checked_results(invalid)

    def test_shared_flavor_ignores_split_without_mutating_properties(self):
        for product, number in ((False, 4), (True, 1)):
            shared = []
            for kind, count in (("full", number), ("half", 2 * number)):
                _, properties = database._checked_results(
                    _pseudoreal_matter((kind, count), product=product)
                )
                original = deepcopy(properties)
                shared.append(database._shared_properties(properties))
                self.assertEqual(properties, original)
                factor = shared[-1]["flavor_symmetry"]["factors"][0]
                self.assertEqual(factor["half_hyper_units"], 2 * number)
                self.assertNotIn("full_hypermultiplets", factor)
                self.assertNotIn("half_hypermultiplets", factor)
            self.assertEqual(shared[0], shared[1])

    def test_existing_full_properties_accept_half_description(self):
        _, full = database._checked_results(_pseudoreal_matter(("full", 4)))
        _, half = database._checked_results(_pseudoreal_matter(("half", 8)))
        for legacy_split, missing_spectrum, json_string in (
            (False, False, True), (True, False, True),
            (True, True, True), (True, False, False),
        ):
            with self.subTest(legacy=legacy_split, spectrum=missing_spectrum, string=json_string):
                stored = database._shared_properties(full)
                if legacy_split:
                    stored["flavor_symmetry"] = deepcopy(full["flavor_symmetry"])
                if missing_spectrum:
                    stored.pop("coulomb_branch_spectrum", None)
                encoded = database._json_text(stored)
                connection = _RecordingConnection(select_rows=[{
                    "properties_json": encoded if json_string else json.loads(encoded)
                }])
                database._insert_shared_properties(connection, theory_id=3, properties=half)
                updates = [
                    params for sql, params in connection.statements
                    if sql.startswith("UPDATE theory_properties")
                ]
                self.assertEqual(len(updates), int(legacy_split))
                if updates:
                    expected = json.loads(database._json_text(database._shared_properties(half)))
                    self.assertEqual(json.loads(updates[0][0]), expected)

    def test_shared_properties_still_reject_physical_mismatches(self):
        _, properties = database._checked_results(_pseudoreal_matter(("half", 8)))
        for change in ("multiplicity", "central_charge"):
            stored = database._shared_properties(properties)
            stored["flavor_symmetry"] = deepcopy(properties["flavor_symmetry"])
            stored = json.loads(database._json_text(stored))
            if change == "multiplicity":
                stored["flavor_symmetry"]["factors"][0]["half_hyper_units"] = 6
            else:
                stored["central_charges"]["a"]["numerator"] += 1
            connection = _RecordingConnection(select_rows=[{"properties_json": stored}])
            with self.assertRaisesRegex(ValueError, "different shared properties"):
                database._insert_shared_properties(connection, theory_id=3, properties=properties)
            self.assertEqual(len(connection.statements), 1)

    def test_flavor_rows_store_units_and_realization_rows_preserve_split(self):
        data = _pseudoreal_matter(("full", 2), ("half", 4))
        checked, properties = database._checked_results(data)
        connection = _RecordingConnection()
        database._insert_shared_properties(connection, theory_id=3, properties=properties)
        flavor_sql, flavor_params = next(
            row for row in connection.statements
            if row[0].startswith("INSERT INTO flavor_symmetry_factors")
        )
        self.assertNotIn("full_hypermultiplets", flavor_sql)
        self.assertNotIn("half_hypermultiplets", flavor_sql)
        self.assertEqual(flavor_sql.count("%s"), len(flavor_params))
        self.assertEqual(flavor_params[7], 8)
        database._insert_realization(connection, 3, database._canonical_hash(checked), data, checked, properties)
        hyper_rows = [
            params for sql, params in connection.statements
            if sql.startswith("INSERT INTO hypermultiplets(")
        ]
        self.assertEqual([params[3:5] for params in hyper_rows], [("full", 2), ("half", 4)])
        realization = next(
            params for sql, params in connection.statements
            if sql.startswith("INSERT INTO lagrangian_realizations")
        )
        self.assertEqual(json.loads(realization[9]), data)

    def test_store_deduplicates_equivalent_splits_in_both_orders(self):
        for product, number in ((False, 4), (True, 1)):
            full = _pseudoreal_matter(("full", number), product=product)
            half = _pseudoreal_matter(("half", 2 * number), product=product)
            for first_data, second_data in ((full, half), (half, full)):
                with self.subTest(product=product, first=first_data):
                    connection = _RecordingConnection()
                    connection.begin = MagicMock()
                    connection.commit = MagicMock()
                    connection.rollback = MagicMock()
                    realizations = {}
                    insert_realization = database._insert_realization

                    def fetchone(connection, statement, parameters=()):
                        if "lr.canonical_hash" in statement:
                            return realizations.get(parameters[0])
                        if "SELECT name FROM theories" in statement:
                            return {"name": "test"}
                        return None

                    def insert(connection, theory_id, canonical_hash, data, checked, properties):
                        realization_id = insert_realization(
                            connection, theory_id, canonical_hash, data, checked, properties
                        )
                        realizations[canonical_hash] = {
                            "realization_id": realization_id, "theory_id": theory_id,
                            "name": "test", "gauge_group": checked["group"],
                        }
                        return realization_id

                    with (
                        patch.object(database, "initialize_database"),
                        patch.object(database, "_fetchone", side_effect=fetchone),
                        patch.object(database, "_insert_realization", side_effect=insert),
                    ):
                        first = database.store_lagrangian_theory(connection, first_data)
                        writes = len(connection.statements)
                        second = database.store_lagrangian_theory(connection, second_data)
                        attached = database.store_lagrangian_theory(
                            connection, second_data, theory_id=first.theory_id
                        )
                        with self.assertRaisesRegex(ValueError, "already attached"):
                            database.store_lagrangian_theory(
                                connection, second_data, theory_id=first.theory_id + 1
                            )
                    self.assertTrue(first.inserted)
                    for result in (second, attached):
                        self.assertFalse(result.inserted)
                        self.assertEqual(result.theory_id, first.theory_id)
                        self.assertEqual(result.lagrangian_realization_id, first.lagrangian_realization_id)
                        self.assertEqual(result.canonical_hash, first.canonical_hash)
                    self.assertEqual(len(connection.statements), writes)
                    self.assertEqual(len(realizations), 1)
                    connection.commit.assert_called_once_with()
                    connection.rollback.assert_not_called()

    def test_initialize_database_executes_schema_and_records_version(self):
        connection = _RecordingConnection()

        database.initialize_database(connection)

        statements = [statement for statement, _ in connection.statements]
        self.assertTrue(
            any(
                statement.startswith("CREATE TABLE")
                for statement in statements
            )
        )
        self.assertTrue(
            any(
                "INSERT INTO schema_metadata" in statement
                for statement in statements
            )
        )
        metadata_parameters = next(
            parameters
            for statement, parameters in connection.statements
            if "INSERT INTO schema_metadata" in statement
        )
        self.assertEqual(metadata_parameters, ("schema_version", "1"))

    def test_initialize_database_accepts_current_version_without_changing_data(self):
        connection = _RecordingConnection(select_rows=[{"metadata_value": "1"}])

        database.initialize_database(connection)

        self.assertTrue(all(
            statement.startswith(("CREATE TABLE IF NOT EXISTS", "SELECT"))
            for statement, _ in connection.statements
        ))

    def test_initialize_database_rejects_unsupported_versions_without_changing_data(self):
        for version in ("0", "2", "8", "invalid"):
            with self.subTest(version=version):
                connection = _RecordingConnection(select_rows=[{"metadata_value": version}])
                with self.assertRaisesRegex(RuntimeError, "unsupported database schema version"):
                    database.initialize_database(connection)
                self.assertTrue(all(
                    statement.startswith(("CREATE TABLE IF NOT EXISTS", "SELECT"))
                    for statement, _ in connection.statements
                ))

    def test_connect_database_uses_pymysql_options(self):
        connection = MagicMock()
        with (
            patch.object(
                database.pymysql, "connect", return_value=connection
            ) as connect,
            patch.object(database, "initialize_database") as initialize,
        ):
            result = database.connect_database(
                "n2_test",
                host="mysql.example",
                port=3307,
                user="researcher",
                password="secret",
                connect_timeout=4,
            )

        self.assertIs(result, connection)
        connect.assert_called_once_with(
            host="mysql.example",
            port=3307,
            user="researcher",
            password="secret",
            database="n2_test",
            unix_socket=None,
            charset="utf8mb4",
            cursorclass=database.DictCursor,
            autocommit=True,
            connect_timeout=4,
        )
        initialize.assert_called_once_with(connection)

    def test_realization_inserts_use_mysql_parameters(self):
        anomaly_result, properties = database._checked_results(E6_SCFT)
        connection = _RecordingConnection()

        realization_id = database._insert_realization(
            connection,
            theory_id=7,
            canonical_hash="a" * 64,
            data=E6_SCFT,
            anomaly_result=anomaly_result,
            properties=properties,
        )

        self.assertEqual(realization_id, 1)
        insert_statements = [
            (statement, parameters)
            for statement, parameters in connection.statements
            if statement.startswith("INSERT")
        ]
        self.assertEqual(len(insert_statements), 5)
        self.assertTrue(
            all("?" not in statement for statement, _ in insert_statements)
        )
        self.assertTrue(
            all("%s" in statement for statement, _ in insert_statements)
        )
        gauge_parameters = next(
            parameters
            for statement, parameters in insert_statements
            if "INSERT INTO gauge_factors" in statement
        )
        self.assertEqual(gauge_parameters[5], 6)
        self.assertEqual(gauge_parameters[10:12], (0, 1))

    def test_product_realization_inserts_every_factor_relation(self):
        anomaly_result, properties = database._checked_results(
            A1_PRODUCT_SCFT
        )
        connection = _RecordingConnection()

        database._insert_realization(
            connection,
            theory_id=9,
            canonical_hash="b" * 64,
            data=A1_PRODUCT_SCFT,
            anomaly_result=anomaly_result,
            properties=properties,
        )

        statements = [
            statement for statement, _ in connection.statements
        ]
        self.assertEqual(
            sum("INSERT INTO gauge_factors" in item for item in statements),
            2,
        )
        self.assertEqual(
            sum(
                "INSERT INTO hypermultiplet_representations" in item
                for item in statements
            ),
            2,
        )
        self.assertEqual(
            sum(
                "INSERT INTO exactly_marginal_couplings" in item
                for item in statements
            ),
            2,
        )

    def test_existing_mysql_json_properties_compare_structurally(self):
        _, properties = database._checked_results(E6_SCFT)
        stored_json = json.dumps(
            json.loads(
                database._json_text(
                    database._shared_properties(properties), canonical=True
                )
            ),
            indent=2,
        )
        connection = _RecordingConnection(
            select_rows=[{"properties_json": stored_json}]
        )

        database._insert_shared_properties(
            connection, theory_id=3, properties=properties
        )

        self.assertEqual(len(connection.statements), 1)
        self.assertTrue(connection.statements[0][0].startswith("SELECT"))

    def test_basic_import_preserves_existing_indices_and_missing_spectrum(self):
        _, properties = database._checked_results(E6_SCFT)
        stored = database._shared_properties(properties)
        stored.update({
            "superconformal_index": "existing index",
            "superconformal_index_order": 24,
            "coulomb_branch_index": "existing Coulomb index",
        })
        connection = _RecordingConnection(select_rows=[{
            "properties_json": database._json_text(stored),
        }])
        database._insert_shared_properties(connection, theory_id=3, properties=properties)
        self.assertEqual(len(connection.statements), 1)

    def test_shared_properties_store_exact_central_charge_fractions(self):
        _, properties = database._checked_results(E6_SCFT)
        connection = _RecordingConnection()

        database._insert_shared_properties(
            connection, theory_id=3, properties=properties
        )

        _, parameters = next(
            item
            for item in connection.statements
            if item[0].startswith("INSERT INTO theory_properties")
        )
        expected = {
            "a": {"numerator": 83, "denominator": 4},
            "c": {"numerator": 22, "denominator": 1},
        }
        self.assertEqual(json.loads(parameters[5]), expected)
        self.assertEqual(parameters[6:9], (None, None, None))
        shared_properties = json.loads(parameters[9])
        self.assertEqual(shared_properties["central_charges"], expected)
        for key in ("superconformal_index", "coulomb_branch_index", "coulomb_branch_spectrum"):
            self.assertNotIn(key, shared_properties)

    def test_insertion_checks_never_call_index_or_spectrum_backends(self):
        with patch.object(theory_properties, "calculate_index_internal") as full, \
             patch.object(theory_properties, "calculate_lagrangian_coulomb_branch_index") as coulomb, \
             patch.object(theory_properties, "coulomb_branch_spectrum_from_gauge_factors") as spectrum:
            database._checked_results(E6_SCFT)
        full.assert_not_called()
        coulomb.assert_not_called()
        spectrum.assert_not_called()


    def test_store_commits_successful_transaction(self):
        connection = MagicMock()
        with (
            patch.object(database, "initialize_database"),
            patch.object(
                database,
                "_fetchone",
                side_effect=[None, {"name": "E6 test"}],
            ),
            patch.object(database, "_insert_theory", return_value=11),
            patch.object(database, "_insert_shared_properties") as shared,
            patch.object(database, "_insert_realization", return_value=12),
            patch.object(database, "_execute"),
        ):
            stored = database.store_lagrangian_theory(
                connection, E6_SCFT, name="E6 test"
            )

        self.assertEqual(stored.theory_id, 11)
        self.assertEqual(stored.lagrangian_realization_id, 12)
        connection.begin.assert_called_once_with()
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        shared.assert_called_once()
        self.assertTrue(shared.call_args.kwargs["new_theory"])

    def test_new_theory_properties_skip_missing_row_lock(self):
        _, properties = database._checked_results(E6_SCFT)
        connection = _RecordingConnection()
        with patch.object(database, "_fetchone", side_effect=AssertionError("unnecessary properties lookup")):
            database._insert_shared_properties(connection, 3, properties, new_theory=True)
        self.assertTrue(any("INSERT INTO theory_properties" in sql for sql, _ in connection.statements))

    def test_attaching_a_realization_still_locks_existing_shared_properties(self):
        _, properties = database._checked_results(E6_SCFT)
        connection = _RecordingConnection(select_rows=[{
            "properties_json": database._json_text(database._shared_properties(properties)),
        }])
        database._insert_shared_properties(connection, 3, properties)
        self.assertEqual(len(connection.statements), 1)
        sql, parameters = connection.statements[0]
        self.assertIn("FOR UPDATE", sql)
        self.assertEqual(parameters, (3,))

    def test_store_rolls_back_failed_transaction(self):
        connection = MagicMock()
        with (
            patch.object(database, "initialize_database"),
            patch.object(database, "_fetchone", return_value=None),
            patch.object(database, "_insert_theory", return_value=11),
            patch.object(
                database,
                "_insert_shared_properties",
                side_effect=database.pymysql.IntegrityError("failed"),
            ),
        ):
            with self.assertRaises(database.pymysql.IntegrityError):
                database.store_lagrangian_theory(connection, E6_SCFT)

        connection.begin.assert_called_once_with()
        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()

    def test_concurrent_duplicate_returns_winner_after_rollback(self):
        winner = {"theory_id": 41, "realization_id": 42, "name": "winner", "gauge_group": "E6"}
        for attached in (None, 41, 99):
            with self.subTest(theory_id=attached):
                connection = MagicMock()
                with patch.object(database, "initialize_database") as initialize, \
                     patch.object(database, "_fetchone", side_effect=[None, winner]), \
                     patch.object(database, "_insert_theory", side_effect=database.pymysql.IntegrityError(1062, "duplicate")):
                    if attached == 99:
                        with self.assertRaisesRegex(ValueError, "already attached"):
                            database.store_lagrangian_theory(connection, E6_SCFT, theory_id=attached, initialize_schema=False)
                    else:
                        stored = database.store_lagrangian_theory(connection, E6_SCFT, theory_id=attached, initialize_schema=False)
                        self.assertFalse(stored.inserted)
                        self.assertEqual((stored.theory_id, stored.lagrangian_realization_id), (41, 42))
                    initialize.assert_not_called()
                connection.rollback.assert_called_once()
                connection.commit.assert_not_called()

    def test_deadlock_and_lock_timeout_retry_only_rolled_back_transactions(self):
        connection = MagicMock()
        with patch.object(database, "_find_stored_realization", return_value=None), \
             patch.object(database, "_checked_results", wraps=database._checked_results) as checked, \
             patch.object(database, "_insert_theory", side_effect=[
                 database.pymysql.OperationalError(1213, "deadlock"),
                 database.pymysql.OperationalError(1205, "lock timeout"), 11,
             ]), \
             patch.object(database, "_insert_shared_properties"), \
             patch.object(database, "_insert_realization", return_value=12), \
             patch.object(database, "_execute"), \
             patch.object(database, "_fetchone", return_value={"name": "winner"}), \
             patch.object(database.time, "sleep"):
            stored = database.store_lagrangian_theory(connection, E6_SCFT, initialize_schema=False)
        self.assertTrue(stored.inserted)
        checked.assert_called_once()
        self.assertEqual(connection.begin.call_count, 3)
        self.assertEqual(connection.rollback.call_count, 2)
        connection.commit.assert_called_once()

    def test_retry_is_bounded_and_unrelated_or_ambiguous_failures_propagate(self):
        for exc, attempts in ((database.pymysql.OperationalError(1213, "deadlock"), 3),
                              (database.pymysql.OperationalError(2006, "connection lost"), 1),
                              (database.pymysql.IntegrityError(1062, "unrelated unique key"), 1)):
            with self.subTest(error=exc):
                connection = MagicMock()
                with patch.object(database, "_find_stored_realization", return_value=None), \
                     patch.object(database, "_insert_theory", side_effect=exc), \
                     patch.object(database.time, "sleep"):
                    with self.assertRaises(type(exc)):
                        database.store_lagrangian_theory(connection, E6_SCFT, initialize_schema=False)
                self.assertEqual(connection.begin.call_count, attempts)
                self.assertEqual(connection.rollback.call_count, attempts)
                connection.commit.assert_not_called()

    def test_worker_connection_skips_schema_initialization(self):
        with patch.object(database.pymysql, "connect") as connect, \
             patch.object(database, "initialize_database") as initialize:
            self.assertIs(database.connect_database("n2_test", initialize_schema=False), connect.return_value)
        initialize.assert_not_called()

    def test_ambiguous_commit_is_not_replayed_and_keeps_original_error(self):
        connection = MagicMock()
        connection.commit.side_effect = database.pymysql.OperationalError(2013, "connection lost during commit")
        connection.rollback.side_effect = database.pymysql.InterfaceError(0, "connection closed")
        with patch.object(database, "_find_stored_realization", return_value=None), \
             patch.object(database, "_insert_theory", return_value=11), \
             patch.object(database, "_insert_shared_properties"), \
             patch.object(database, "_insert_realization", return_value=12), \
             patch.object(database, "_execute"):
            with self.assertRaisesRegex(RuntimeError, "connection lost during commit.*rollback failed"):
                database.store_lagrangian_theory(connection, E6_SCFT, initialize_schema=False)
        connection.begin.assert_called_once()
        connection.commit.assert_called_once()

    def test_rejects_anomalous_theory_before_transaction(self):
        connection = MagicMock()
        data = {
            "algebra": "A1",
            "hypermultiplets": [
                {
                    "representation": "fundamental",
                    "kind": "half",
                    "number": 1,
                }
            ],
        }
        with patch.object(database, "initialize_database"):
            with self.assertRaisesRegex(
                database.TheoryCheckError, "not gauge-anomaly-free"
            ):
                database.store_lagrangian_theory(connection, data)
        connection.begin.assert_not_called()

    def test_rejects_nonconformal_theory_before_transaction(self):
        connection = MagicMock()
        data = {"algebra": "A2", "hypermultiplets": []}
        with patch.object(database, "initialize_database"):
            with self.assertRaisesRegex(
                database.TheoryCheckError, "not conformal"
            ):
                database.store_lagrangian_theory(connection, data)
        connection.begin.assert_not_called()


@unittest.skipUnless(
    shutil.which("form") and shutil.which("lie"), "FORM and LiE are required"
)
class TheoryDatabaseBackendTests(unittest.TestCase):
    def test_equivalent_splits_have_equal_actual_indices_and_shared_properties(self):
        with (
            tempfile.TemporaryDirectory() as cache_directory,
            patch.multiple(
                theory_properties,
                INDEX_MAX_ORDER=6,
                C_INDEX_MAX_ORDER=12,
                DEFAULT_PROCESS_COUNT=1,
                CHAR_CACHE_DATABASE_PATH=Path(cache_directory) / "characters.db",
            ),
        ):
            for product, number in ((False, 4), (True, 1)):
                with self.subTest(product=product):
                    checked_full, full = database._checked_results(
                        _pseudoreal_matter(("full", number), product=product)
                    )
                    checked_half, half = database._checked_results(
                        _pseudoreal_matter(("half", 2 * number), product=product)
                    )
                    self.assertEqual(
                        database._canonical_hash(checked_full),
                        database._canonical_hash(checked_half),
                    )
                    full_indices = theory_properties.calculate_n2_theory_indices(
                        _pseudoreal_matter(("full", number), product=product)
                    )
                    half_indices = theory_properties.calculate_n2_theory_indices(
                        _pseudoreal_matter(("half", 2 * number), product=product)
                    )
                    self.assertIsNotNone(full_indices["superconformal_index"])
                    self.assertIsNotNone(full_indices["coulomb_branch_index"])
                    self.assertEqual(full_indices, half_indices)
                    self.assertEqual(
                        database._shared_properties(full),
                        database._shared_properties(half),
                    )
                    connection = _RecordingConnection(select_rows=[{
                        "properties_json": database._json_text(
                            database._shared_properties(full)
                        )
                    }])
                    database._insert_shared_properties(connection, 3, half)
                    self.assertEqual(len(connection.statements), 1)


MYSQL_TEST_DATABASE = os.environ.get("N2_TEST_MYSQL_DATABASE")


@unittest.skipUnless(
    MYSQL_TEST_DATABASE,
    "set N2_TEST_MYSQL_DATABASE to run live MySQL integration tests",
)
class TheoryDatabaseMySQLIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "test" not in MYSQL_TEST_DATABASE.lower():
            raise RuntimeError(
                "N2_TEST_MYSQL_DATABASE must name a dedicated test database"
            )
        cls.settings = {
            "host": os.environ.get("N2_TEST_MYSQL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("N2_TEST_MYSQL_PORT", "3306")),
            "user": os.environ.get("N2_TEST_MYSQL_USER", "root"),
            "password": os.environ.get("N2_TEST_MYSQL_PASSWORD", ""),
            "unix_socket": os.environ.get("N2_TEST_MYSQL_UNIX_SOCKET"),
        }
        cls.connection = database.connect_database(
            MYSQL_TEST_DATABASE, **cls.settings
        )

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        database._execute(self.connection, "DELETE FROM theories")

    def test_writes_and_reads_mysql_database(self):
        stored = database.store_lagrangian_theory(
            self.connection, E6_SCFT, name="E6 MySQL test"
        )

        row = database._fetchone(
            self.connection,
            """
            SELECT
                t.name,
                p.flavor_symmetry,
                p.conformal_manifold_dimension,
                p.central_charges_json,
                p.central_charge_a_decimal,
                p.central_charge_c_decimal
            FROM theories AS t
            JOIN theory_properties AS p ON p.theory_id = t.id
            WHERE t.id = %s
            """,
            (stored.theory_id,),
        )
        central_charges = json.loads(row.pop("central_charges_json"))
        a_decimal = row.pop("central_charge_a_decimal")
        c_decimal = row.pop("central_charge_c_decimal")
        self.assertEqual(
            row,
            {
                "name": "E6 MySQL test",
                "flavor_symmetry": "U(4)",
                "conformal_manifold_dimension": 1,
            },
        )
        self.assertEqual(
            central_charges,
            {
                "a": {"numerator": 83, "denominator": 4},
                "c": {"numerator": 22, "denominator": 1},
            },
        )
        self.assertEqual(a_decimal, Decimal("20.75"))
        self.assertEqual(c_decimal, Decimal("22"))

    def test_file_api_writes_existing_json_to_mysql(self):
        input_path = PROJECT_ROOT / "anomalies" / "example_e6.json"

        stored = database.store_lagrangian_theory_from_file(
            MYSQL_TEST_DATABASE,
            input_path,
            name="E6 file API test",
            **self.settings,
        )

        row = database._fetchone(
            self.connection,
            """
            SELECT input_json
            FROM lagrangian_realizations
            WHERE id = %s
            """,
            (stored.lagrangian_realization_id,),
        )
        self.assertEqual(json.loads(row["input_json"])["algebra"], "E6")


if __name__ == "__main__":
    unittest.main()
