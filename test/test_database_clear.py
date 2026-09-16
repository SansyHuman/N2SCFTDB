"""Deletion/authentication checks using doubles and the dedicated test database."""

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from common import n2_theory_db as db
from gui import database_clear


SETTINGS = {
    "mysql/database": "clear_test", "mysql/host": "db.invalid", "mysql/port": 3311,
    "mysql/user": "tester", "mysql/password": "old saved secret",
    "mysql/connect_timeout": 9, "mysql/unix_socket": "/tmp/test-mysql.sock",
}


class DatabaseClearTests(unittest.TestCase):
    def test_only_confirmed_fresh_password_is_used_without_schema_initialization(self):
        connection = MagicMock()
        with patch.object(db, "connect_database", return_value=connection) as connect, \
             patch.object(db, "delete_all_theory_data", return_value=12) as delete:
            self.assertEqual(database_clear.clear_database(SETTINGS, " fresh secret ", confirmed=True), 12)
        connect.assert_called_once_with(
            "clear_test", host="db.invalid", port=3311, user="tester", password=" fresh secret ",
            unix_socket="/tmp/test-mysql.sock", connect_timeout=9, initialize_schema=False,
        )
        delete.assert_called_once_with(connection)
        connection.close.assert_called_once()

    def test_cancel_missing_password_and_authentication_failure_never_delete(self):
        with patch.object(db, "connect_database") as connect, \
             patch.object(db, "delete_all_theory_data") as delete:
            for options in ({}, {"confirmed": "yes"}, {"confirmed": 1}):
                with self.assertRaises(ValueError):
                    database_clear.clear_database(SETTINGS, "entered", **options)
            with self.assertRaises(ValueError):
                database_clear.clear_database(SETTINGS, "", confirmed=True)
            connect.assert_not_called()
            connect.side_effect = db.pymysql.OperationalError(1045, "wrong password")
            with self.assertRaises(db.pymysql.OperationalError):
                database_clear.clear_database(SETTINGS, "wrong", confirmed=True)
            delete.assert_not_called()

    def test_failed_deletion_rolls_back_and_closes(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"metadata_value": str(db.SCHEMA_VERSION)}
        cursor.execute.side_effect = [1, RuntimeError("delete failed")]
        with patch.object(db, "connect_database", return_value=connection), self.assertRaises(RuntimeError):
            database_clear.clear_database(SETTINGS, "entered", confirmed=True)
        connection.begin.assert_called_once()
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    def test_worker_errors_do_not_disclose_passwords_or_publish_success(self):
        request = {"settings": SETTINGS, "password": "new secret", "confirm_delete": True}
        for error in (db.pymysql.OperationalError(1045, "new secret"), RuntimeError("old saved secret")):
            output = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(request))), \
                 patch.object(database_clear, "clear_database", side_effect=error), redirect_stdout(output):
                self.assertEqual(database_clear.main(), 1)
            result = json.loads(output.getvalue())
            self.assertIn("error", result)
            self.assertNotIn("deleted", result)
            self.assertNotIn("new secret", output.getvalue())
            self.assertNotIn("old saved secret", output.getvalue())


MYSQL_DATABASE = os.environ.get("N2_TEST_MYSQL_DATABASE")


@unittest.skipUnless(MYSQL_DATABASE, "requires the dedicated MySQL test database")
class DatabaseClearMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "test" not in MYSQL_DATABASE.lower():
            raise RuntimeError("N2_TEST_MYSQL_DATABASE must name a dedicated test database")
        cls.options = {
            "host": os.environ.get("N2_TEST_MYSQL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("N2_TEST_MYSQL_PORT", "3306")),
            "user": os.environ.get("N2_TEST_MYSQL_USER", "root"),
            "password": os.environ.get("N2_TEST_MYSQL_PASSWORD", ""),
            "unix_socket": os.environ.get("N2_TEST_MYSQL_UNIX_SOCKET"),
        }
        cls.connection = db.connect_database(MYSQL_DATABASE, **cls.options)
        cls.settings = {"mysql/" + key: value for key, value in cls.options.items()}
        cls.settings.update({"mysql/database": MYSQL_DATABASE, "mysql/connect_timeout": 10})

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        db._execute(self.connection, "DELETE FROM theories")
        self.stored = db.store_lagrangian_theory(self.connection, {
            "algebra": "A1", "hypermultiplets": [{"representation": "fundamental", "number": 4}],
        })
        db._execute(self.connection, """INSERT INTO non_lagrangian_realizations
            (theory_id, construction_type, data_json) VALUES (%s, 'test', '{}')""",
            (self.stored.theory_id,))
        db._execute(self.connection, """UPDATE theory_properties SET
            superconformal_index_json = '"1 + t^4"', superconformal_index_order = 4,
            coulomb_branch_index_json = '"1"', coulomb_branch_spectrum_json = '{"2": 1}'
            WHERE theory_id = %s""", (self.stored.theory_id,))

    def test_correct_password_clears_every_data_table_and_database_remains_usable(self):
        if not self.options["password"]:
            self.skipTest("requires a password-protected test account")
        metadata = db._fetchone(self.connection, "SELECT * FROM schema_metadata WHERE metadata_key='schema_version'")
        count = database_clear.clear_database(self.settings, self.options["password"], confirmed=True)
        self.assertEqual(count, 1)
        with self.connection.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            tables = [next(iter(row.values())) for row in cursor.fetchall()]
        for table in tables:
            if table != "schema_metadata":
                with self.subTest(table=table):
                    row = db._fetchone(self.connection, f"SELECT COUNT(*) AS n FROM `{table.replace('`', '``')}`")
                    self.assertEqual(row["n"], 0)
        self.assertEqual(db._fetchone(self.connection, "SELECT * FROM schema_metadata WHERE metadata_key='schema_version'"), metadata)
        again = db.store_lagrangian_theory(self.connection, {
            "algebra": "A1", "hypermultiplets": [{"representation": "fundamental", "number": 4}],
        })
        self.assertTrue(again.inserted)

    def test_wrong_password_preserves_rows(self):
        with self.assertRaises(db.pymysql.OperationalError):
            database_clear.clear_database(self.settings, self.options["password"] + "-wrong", confirmed=True)
        self.assertEqual(db._fetchone(self.connection, "SELECT COUNT(*) AS n FROM theories")["n"], 1)
        self.assertEqual(db._fetchone(self.connection, "SELECT COUNT(*) AS n FROM theory_properties")["n"], 1)

    def test_real_worker_authenticates_over_stdin_before_deleting(self):
        if not self.options["password"]:
            self.skipTest("requires a password-protected test account")
        for password, success in ((self.options["password"] + "-wrong", False),
                                  (self.options["password"], True)):
            request = {"settings": self.settings, "password": password, "confirm_delete": True}
            result = subprocess.run(
                [sys.executable, "-B", "-m", "gui.database_clear"],
                cwd=Path(__file__).resolve().parents[1], input=json.dumps(request),
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotIn(password, result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0 if success else 1)
            reply = json.loads(result.stdout)
            self.assertIn("deleted" if success else "error", reply)
            self.assertEqual(db._fetchone(self.connection, "SELECT COUNT(*) AS n FROM theories")["n"],
                             0 if success else 1)

    def test_current_schema_is_required_and_failure_rolls_back_cascades(self):
        db._execute(self.connection, "UPDATE schema_metadata SET metadata_value='0' WHERE metadata_key='schema_version'")
        try:
            with self.assertRaises(ValueError):
                db.delete_all_theory_data(self.connection)
        finally:
            db._execute(self.connection, "UPDATE schema_metadata SET metadata_value=%s WHERE metadata_key='schema_version'", (str(db.SCHEMA_VERSION),))
        db.store_lagrangian_theory(self.connection, {
            "algebra": "A2", "hypermultiplets": [{"representation": "fundamental", "number": 6}],
        })
        # Execute the real cascading DELETE, then fail before COMMIT. The
        # test needs only database-scoped privileges, including with binlogging.
        with patch.object(self.connection, "commit", side_effect=db.pymysql.OperationalError(
                1205, "injected failure before commit")):
            with self.assertRaises(db.pymysql.MySQLError):
                db.delete_all_theory_data(self.connection)
        self.assertEqual(db._fetchone(self.connection, "SELECT COUNT(*) AS n FROM theories")["n"], 2)
        self.assertEqual(db._fetchone(self.connection, "SELECT COUNT(*) AS n FROM theory_properties")["n"], 2)
        self.assertEqual(db._fetchone(self.connection, "SELECT COUNT(*) AS n FROM non_lagrangian_realizations")["n"], 1)


if __name__ == "__main__":
    unittest.main()
