from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from fractions import Fraction
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import MagicMock, patch

from common import n2_theory_db as db
from common import n2_theory_db_indices as worker
from common import n2_theory_properties as properties
from test.test_n2_theory_db import (
    E6_SCFT, MYSQL_TEST_DATABASE, _RecordingConnection,
)


SU2 = {
    "algebra": "A1",
    "hypermultiplets": [{"representation": "fundamental", "number": 4}],
}


def index_payload(order=4, maximum=5):
    return {
        "superconformal_index": f"1 + t^{order}",
        "superconformal_index_order": order,
        "coulomb_branch_index": "1 + x^2 + x^4",
        "coulomb_branch_index_max_dimension": maximum,
        "coulomb_branch_spectrum": (Fraction(2),),
    }


class IndexUpdateUnitTests(unittest.TestCase):
    def test_migration_adds_nullable_cutoffs_without_overwriting_legacy_indices(self):
        connection = _RecordingConnection(select_rows=[{"metadata_value": "6"}])
        db.initialize_database(connection)
        alterations = [sql for sql, _ in connection.statements if sql.startswith("ALTER TABLE")]
        self.assertEqual(len(alterations), 1)
        self.assertIn("superconformal_index_order BIGINT UNSIGNED NULL", alterations[0])
        self.assertIn("coulomb_branch_index_max_dimension_json JSON NULL", alterations[0])
        self.assertFalse(any(sql.startswith("UPDATE theory_properties") for sql, _ in connection.statements))
        self.assertEqual(connection.statements[-1][1], ("7", "schema_version"))

    def test_invalid_payloads_fail_before_transaction(self):
        connection = MagicMock()
        for payload in (
            {"superconformal_index": "1"},
            {"superconformal_index_order": 5},
            {"superconformal_index": "", "superconformal_index_order": 5},
            {"superconformal_index": "1", "superconformal_index_order": -1},
            {"superconformal_index": "1", "superconformal_index_order": True},
            {"superconformal_index": "1", "superconformal_index_order": 2**64},
            {"coulomb_branch_index": "1", "coulomb_branch_index_max_dimension": 2.5},
            {"coulomb_branch_index": "1", "coulomb_branch_index_max_dimension": {"numerator": 1, "denominator": 0}},
            {"coulomb_branch_spectrum": [0]},
            {"coulomb_branch_spectrum": "x^2"},
            {"typo": "1"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                db.update_lagrangian_indices(connection, 1, payload)
        connection.begin.assert_not_called()

    def test_worker_limit_zero_does_not_query_or_calculate(self):
        connection = MagicMock()
        self.assertEqual(list(worker.fill_lagrangian_indices(connection, limit=0)), [])
        connection.cursor.assert_not_called()

    def test_cli_invalid_cutoff_never_connects(self):
        with patch.object(db, "connect_database") as connect, redirect_stderr(StringIO()):
            self.assertEqual(worker.main(["unused_test", "--index-order", "-1"]), 2)
        connect.assert_not_called()

    def test_worker_reports_errors_and_continues_other_theories(self):
        jobs = [{
            "theory_id": i, "lagrangian_realization_id": i, "input": SU2,
            "needed_fields": ["superconformal_index"], "unknown_precision": [],
        } for i in (1, 2)]
        with patch.object(db, "iter_lagrangian_index_jobs", return_value=iter(jobs)), \
             patch.object(properties, "calculate_superconformal_index", side_effect=[RuntimeError("FORM failed"), "1"]), \
             patch.object(db, "update_lagrangian_indices", return_value={
                 "updated_fields": ["superconformal_index"], "skipped_fields": {},
             }) as save:
            results = list(worker.fill_lagrangian_indices(MagicMock(), order=0))
        self.assertEqual(results[0]["errors"], {"superconformal_index": "FORM failed"})
        self.assertEqual(results[1]["errors"], {})
        self.assertEqual(save.call_args.args[1], 2)
        save.assert_called_once()


@unittest.skipUnless(MYSQL_TEST_DATABASE, "set N2_TEST_MYSQL_DATABASE for live MySQL tests")
class IndexUpdateMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "test" not in MYSQL_TEST_DATABASE.lower():
            raise RuntimeError("N2_TEST_MYSQL_DATABASE must name a dedicated test database")
        cls.settings = {
            "host": os.environ.get("N2_TEST_MYSQL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("N2_TEST_MYSQL_PORT", "3306")),
            "user": os.environ.get("N2_TEST_MYSQL_USER", "root"),
            "password": os.environ.get("N2_TEST_MYSQL_PASSWORD", ""),
            "unix_socket": os.environ.get("N2_TEST_MYSQL_UNIX_SOCKET"),
        }
        cls.connection = db.connect_database(MYSQL_TEST_DATABASE, **cls.settings)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        db._execute(self.connection, "DELETE FROM theories")
        self.stored = db.store_lagrangian_theory(self.connection, SU2)
        self.rid = self.stored.lagrangian_realization_id

    def row(self):
        return db._fetchone(self.connection, "SELECT * FROM theory_properties WHERE theory_id = %s", (self.stored.theory_id,))

    def save(self, **kwargs):
        return db.update_lagrangian_indices(self.connection, self.rid, index_payload(**kwargs))

    def test_basic_storage_then_real_worker_then_idempotent_resume(self):
        before = self.row()
        for column in db._INDEX_COLUMNS.values():
            self.assertIsNone(before[column])
        with tempfile.TemporaryDirectory() as cache, patch.multiple(
            properties, INDEX_CACHE_DIRECTORY=Path(cache), DEFAULT_PROCESS_COUNT=1,
        ):
            results = list(worker.fill_lagrangian_indices(self.connection, order=4, max_dimension=5))
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["errors"])
        after = self.row()
        from index.n2_theory_index import parse_index_polynomial
        self.assertEqual(
            parse_index_polynomial(json.loads(after["superconformal_index_json"])),
            parse_index_polynomial("1 + 28*t^4/u^2 + t^4*u^4"),
        )
        self.assertEqual(json.loads(after["coulomb_branch_index_json"]), "1 + x^2 + x^4")
        self.assertEqual(after["superconformal_index_order"], 4)
        self.assertEqual(json.loads(after["coulomb_branch_index_max_dimension_json"]), {"numerator": 5, "denominator": 1})
        self.assertEqual(after["central_charges_json"], before["central_charges_json"])
        combined = json.loads(after["properties_json"])
        for key, column in db._INDEX_COLUMNS.items():
            actual = after[column] if key == "superconformal_index_order" else json.loads(after[column])
            self.assertEqual(combined[key], actual)
        with patch.object(properties, "calculate_superconformal_index") as full:
            self.assertEqual(list(worker.fill_lagrangian_indices(self.connection)), [])
            self.assertEqual(list(worker.fill_lagrangian_indices(self.connection, order=4, max_dimension=5, upgrade=True)), [])
            full.assert_not_called()
        duplicate = db.store_lagrangian_theory(self.connection, SU2, name="ignored")
        self.assertFalse(duplicate.inserted)
        self.assertEqual(self.row(), after)

    def test_independent_upgrade_keeps_lower_other_index(self):
        self.save(order=12, maximum=20)
        before = self.row()
        result = self.save(order=18, maximum=10)
        after = self.row()
        self.assertEqual(after["superconformal_index_order"], 18)
        self.assertEqual(after["coulomb_branch_index_json"], before["coulomb_branch_index_json"])
        self.assertEqual(after["coulomb_branch_index_max_dimension_json"], before["coulomb_branch_index_max_dimension_json"])
        self.assertEqual(result["skipped_fields"]["coulomb_branch_index"], "lower_order")
        self.save(order=6, maximum=Fraction(81, 4))
        self.assertEqual(self.row()["superconformal_index_order"], 18)
        self.assertEqual(json.loads(self.row()["coulomb_branch_index_max_dimension_json"]), {"numerator": 81, "denominator": 4})

    def test_equal_and_lower_cutoffs_are_noops_even_with_different_strings(self):
        self.save(order=12, maximum=20)
        db._execute(self.connection, "UPDATE theories SET updated_at = '2000-01-01 00:00:00' WHERE id = %s", (self.stored.theory_id,))
        before = self.row()
        for order, maximum in ((12, 20), (0, 0)):
            payload = index_payload(order, maximum)
            payload["superconformal_index"] = "different result"
            payload["coulomb_branch_index"] = "different result"
            result = db.update_lagrangian_indices(self.connection, self.rid, payload)
            self.assertEqual(result["updated_fields"], [])
            self.assertEqual(self.row(), before)
        timestamp = db._fetchone(self.connection, "SELECT updated_at FROM theories WHERE id = %s", (self.stored.theory_id,))["updated_at"]
        self.assertEqual(timestamp.year, 2000)

    def test_conflicting_spectrum_rolls_back_whole_update(self):
        self.save()
        before = self.row()
        payload = index_payload(order=10, maximum=20)
        payload["coulomb_branch_spectrum"] = (Fraction(3),)
        with self.assertRaisesRegex(ValueError, "spectrum conflicts"):
            db.update_lagrangian_indices(self.connection, self.rid, payload)
        self.assertEqual(self.row(), before)

    def test_failure_rolls_back_columns_and_combined_json(self):
        before = self.row()
        original = db._execute
        def fail_timestamp(connection, sql, params=()):
            if "UPDATE theories SET updated_at" in " ".join(sql.split()):
                raise RuntimeError("simulated write failure")
            return original(connection, sql, params)
        with patch.object(db, "_execute", side_effect=fail_timestamp), self.assertRaises(RuntimeError):
            self.save()
        self.assertEqual(self.row(), before)

    def test_unknown_realization_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown Lagrangian"):
            db.update_lagrangian_indices(self.connection, 2**63, index_payload())
        self.assertIsNone(self.row()["superconformal_index_json"])

    def test_partial_failure_persists_success_and_resume_only_retries_missing(self):
        with patch.object(properties, "calculate_superconformal_index", return_value="1") as full, \
             patch.object(properties, "calculate_coulomb_branch_index", side_effect=RuntimeError("FORM failed")):
            result = list(worker.fill_lagrangian_indices(self.connection, order=0, max_dimension=5))[0]
        self.assertIn("coulomb_branch_index", result["errors"])
        self.assertEqual(json.loads(self.row()["superconformal_index_json"]), "1")
        self.assertIsNotNone(self.row()["coulomb_branch_spectrum_json"])
        with patch.object(properties, "calculate_superconformal_index") as full, \
             patch.object(properties, "calculate_coulomb_branch_spectrum") as spectrum, \
             patch.object(properties, "calculate_coulomb_branch_index", return_value="1 + x^2 + x^4"):
            retry = list(worker.fill_lagrangian_indices(self.connection, order=0, max_dimension=5))[0]
        self.assertFalse(retry["errors"])
        full.assert_not_called()
        spectrum.assert_not_called()

    def test_json_null_indices_are_selected_as_missing(self):
        db._execute(self.connection, """
            UPDATE theory_properties SET superconformal_index_json = CAST('null' AS JSON),
                coulomb_branch_index_json = CAST('null' AS JSON),
                coulomb_branch_spectrum_json = CAST('null' AS JSON)
            WHERE theory_id = %s
        """, (self.stored.theory_id,))
        jobs = list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0))
        self.assertEqual(jobs[0]["needed_fields"], [
            "superconformal_index", "coulomb_branch_index", "coulomb_branch_spectrum",
        ])
        self.save()
        self.assertEqual(list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0)), [])

    def test_upgrade_worker_only_computes_higher_requested_components(self):
        self.save(order=4, maximum=10)
        with patch.object(properties, "calculate_superconformal_index", return_value="1") as full, \
             patch.object(properties, "calculate_coulomb_branch_index") as coulomb, \
             patch.object(properties, "calculate_coulomb_branch_spectrum") as spectrum:
            result = list(worker.fill_lagrangian_indices(self.connection, order=6, max_dimension=5, upgrade=True))
        self.assertEqual(len(result), 1)
        full.assert_called_once()
        coulomb.assert_not_called()
        spectrum.assert_not_called()
        self.assertEqual(self.row()["superconformal_index_order"], 6)

    def test_paging_limit_and_one_realization_per_shared_theory(self):
        # A second realization row shares the same physical property record.
        db._execute(self.connection, """
            INSERT INTO lagrangian_realizations (
                theory_id, canonical_hash, gauge_group, gauge_factor_count,
                perturbative_gauge_anomaly_free, global_gauge_anomaly_free,
                anomaly_free, one_loop_beta_vanishes, lagrangian_scft_candidate,
                input_json, anomaly_result_json, exactly_marginal_couplings_json
            ) SELECT theory_id, %s, gauge_group, gauge_factor_count,
                perturbative_gauge_anomaly_free, global_gauge_anomaly_free,
                anomaly_free, one_loop_beta_vanishes, lagrangian_scft_candidate,
                input_json, anomaly_result_json, exactly_marginal_couplings_json
            FROM lagrangian_realizations WHERE id = %s
        """, ("f" * 64, self.rid))
        other = db.store_lagrangian_theory(self.connection, E6_SCFT)
        jobs = list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0, batch_size=1))
        self.assertEqual([j["lagrangian_realization_id"] for j in jobs], [self.rid, other.lagrangian_realization_id])
        with patch.object(properties, "calculate_superconformal_index", return_value="1"), \
             patch.object(properties, "calculate_coulomb_branch_index", return_value="1"):
            results = list(worker.fill_lagrangian_indices(self.connection, order=0, max_dimension=0, limit=1))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0))), 1)

    def test_legacy_migration_preserves_data_and_precision_can_be_recorded(self):
        self.save(order=12, maximum=20)
        before = self.row()
        # Reconstruct the version-6 shape on this dedicated test database.
        db._execute(self.connection, """
            UPDATE theory_properties SET properties_json = JSON_REMOVE(
                properties_json, '$.superconformal_index_order', '$.coulomb_branch_index_max_dimension')
        """)
        db._execute(self.connection, """
            ALTER TABLE theory_properties DROP COLUMN superconformal_index_order,
                DROP COLUMN coulomb_branch_index_max_dimension_json
        """)
        db._execute(self.connection, "UPDATE schema_metadata SET metadata_value = '6' WHERE metadata_key = 'schema_version'")
        db.initialize_database(self.connection)
        db.initialize_database(self.connection)
        state = self.row()
        self.assertEqual(state["superconformal_index_json"], before["superconformal_index_json"])
        self.assertEqual(state["coulomb_branch_index_json"], before["coulomb_branch_index_json"])
        self.assertIsNone(state["superconformal_index_order"])
        result = self.save(order=100, maximum=100)
        self.assertEqual(result["skipped_fields"]["superconformal_index"], "unknown_precision")
        self.assertEqual(self.row(), state)
        results = list(worker.fill_lagrangian_indices(self.connection, upgrade=True))
        self.assertEqual(results[0]["skipped_fields"]["coulomb_branch_index"], "unknown_precision")
        db.record_lagrangian_index_cutoffs(self.connection, self.rid, order=12, max_dimension=20)
        with self.assertRaisesRegex(ValueError, "different known cutoff"):
            db.record_lagrangian_index_cutoffs(self.connection, self.rid, order=8)
        self.save(order=14, maximum=21)
        self.assertEqual(self.row()["superconformal_index_order"], 14)

    def test_recording_cutoff_requires_an_existing_index(self):
        with self.assertRaisesRegex(ValueError, "missing superconformal_index"):
            db.record_lagrangian_index_cutoffs(self.connection, self.rid, order=12)
        self.assertIsNone(self.row()["superconformal_index_order"])

    def test_concurrent_updates_cannot_downgrade(self):
        self.save()
        barrier = Barrier(2)
        connections = [db.connect_database(MYSQL_TEST_DATABASE, **self.settings) for _ in range(2)]
        def save(connection, order):
            barrier.wait(timeout=10)
            return db.update_lagrangian_indices(connection, self.rid, index_payload(order, order))
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(save, connection, order) for connection, order in zip(connections, (20, 30))]
                for future in futures:
                    future.result(timeout=15)
        finally:
            for connection in connections:
                connection.close()
        self.assertEqual(self.row()["superconformal_index_order"], 30)
        self.assertEqual(json.loads(self.row()["coulomb_branch_index_max_dimension_json"]), {"numerator": 30, "denominator": 1})

    def test_cli_runs_worker_and_reports_summary(self):
        output, error_output = StringIO(), StringIO()
        with tempfile.TemporaryDirectory() as cache, redirect_stdout(output), patch.multiple(
            properties, INDEX_CACHE_DIRECTORY=Path(cache), DEFAULT_PROCESS_COUNT=1,
        ), patch.dict(os.environ, {
            # The CLI uses N2_DB_*; this fixture uses N2_TEST_MYSQL_*.
            "N2_DB_PASSWORD": self.settings["password"],
            "N2_DB_UNIX_SOCKET": self.settings["unix_socket"] or "",
        }), redirect_stderr(error_output):
            code = worker.main([
                MYSQL_TEST_DATABASE, "--user", self.settings["user"],
                "--host", self.settings["host"], "--port", str(self.settings["port"]),
                *(["--unix-socket", self.settings["unix_socket"]] if self.settings["unix_socket"] else []),
                "--index-order", "0", "--coulomb-max-dimension", "0",
                "--cache-directory", cache, "--processes", "1",
            ])
        self.assertEqual(code, 0, error_output.getvalue())
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), {"processed": 1, "failed": 0})


if __name__ == "__main__":
    unittest.main()
