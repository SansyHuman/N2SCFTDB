"""Offscreen deletion-dialog checks; fixture processes never access a database."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtCore, QtTest, QtWidgets

from gui.database_clear_dialog import DeleteDatabaseDialog
from gui.n2_db import N2DatabaseWindow, SettingsDialog, SettingsStore, default_settings
from test.test_gui_n2_db import MemoryVault


class DeleteDatabaseGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="delete-gui-test-")
        self.addCleanup(self.temporary.cleanup)
        self.settings = default_settings()
        self.settings.update({"mysql/database": "saved_test", "mysql/user": "tester",
                              "mysql/password": "saved credential"})
        self.store = SettingsStore(Path(self.temporary.name) / "settings.ini", vault=MemoryVault())
        self.store.save(self.settings)
        self.launches = []

    def fixture(self, *, reply=None, code=0, delay=0.05, password=" entered secret "):
        reply = {"deleted": 7} if reply is None else reply
        script = f"""
import json, sys, time
request = json.loads(sys.stdin.readline())
assert request['password'] == {password!r}
assert request['confirm_delete'] is True
assert 'mysql/password' not in request['settings']
assert request['settings']['mysql/database'] == 'saved_test'
time.sleep({delay!r})
print(json.dumps({reply!r}), flush=True)
sys.exit({code})
"""
        launches = self.launches

        class FixtureProcess(QtCore.QProcess):
            def start(self, program, arguments):
                launches.append((program, arguments))
                super().start(sys.executable, ["-B", "-u", "-c", script])

        return patch("gui.database_clear_dialog.QtCore.QProcess", FixtureProcess)

    def wait(self, dialog):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while dialog.process is not None and timer.elapsed() < 10000:
            QtTest.QTest.qWait(10)
        self.assertIsNone(dialog.process)

    def test_settings_button_opens_warning_for_displayed_target_without_connecting(self):
        settings = SettingsDialog(self.store)
        self.addCleanup(settings.close)
        settings.databaseEdit.setText("another_test")
        settings.userEdit.setText("another_user")
        seen = []

        def cancel():
            dialog = self.app.activeModalWidget()
            seen.append(dialog)
            self.assertIsInstance(dialog, DeleteDatabaseDialog)
            self.assertIn("another_test", dialog.targetLabel.text())
            self.assertIn("another_user", dialog.targetLabel.text())
            self.assertIn("cannot be undone", dialog.warningLabel.text())
            self.assertEqual(dialog.passwordEdit.text(), "")
            self.assertEqual(dialog.passwordEdit.echoMode(), QtWidgets.QLineEdit.EchoMode.Password)
            self.assertFalse(dialog.deleteButton.isEnabled())
            dialog.cancelButton.click()

        QtCore.QTimer.singleShot(0, cancel)
        with patch("gui.database_clear_dialog.QtCore.QProcess") as process, \
             patch.object(self.store, "save") as save:
            settings.deleteDatabaseContentsButton.click()
        self.assertEqual(len(seen), 1)
        process.assert_not_called()
        save.assert_not_called()
        self.assertEqual(self.store.load()["mysql/database"], "saved_test")

    def test_password_authentication_runs_only_after_delete_and_close_waits(self):
        dialog = DeleteDatabaseDialog(self.settings)
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.passwordEdit.setText(" entered secret ")
        self.assertIsNone(dialog.process)
        with self.fixture(delay=0.15):
            dialog.deleteButton.click()
            self.assertIsNotNone(dialog.process)
            self.assertFalse(dialog.passwordEdit.isEnabled())
            self.assertFalse(dialog.cancelButton.isEnabled())
            dialog._delete()  # Repeated signals cannot start a second deletion.
            dialog.reject()
            self.assertFalse(dialog.close())
            self.wait(dialog)
        self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.deleted_count, 7)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0][1], ["-python", "-B", "-u", "-m", "gui.database_clear"])
        self.assertNotIn("entered secret", repr(self.launches))
        self.assertEqual(dialog._request, b"")
        self.assertEqual(dialog._password, "")
        self.assertEqual(self.store.load()["mysql/password"], "saved credential")

    def test_failure_is_redacted_and_requires_new_password_for_retry(self):
        dialog = DeleteDatabaseDialog(self.settings)
        self.addCleanup(dialog.close)
        dialog.passwordEdit.setText(" entered secret ")
        with self.fixture(reply={"error": "Rejected entered secret password"}, code=1):
            # Match the full secret in the returned error to test redaction.
            dialog.deleteButton.click()
            self.wait(dialog)
        self.assertIsNone(dialog.deleted_count)
        self.assertIn("[redacted]", dialog.statusLabel.text())
        self.assertNotIn("entered secret", dialog.statusLabel.text())
        self.assertEqual(dialog.passwordEdit.text(), "")
        self.assertFalse(dialog.deleteButton.isEnabled())
        self.assertTrue(dialog.cancelButton.isEnabled())
        dialog.passwordEdit.setText(" entered secret ")
        with self.fixture():
            dialog.deleteButton.click()
            self.wait(dialog)
        self.assertEqual(dialog.deleted_count, 7)

    def test_worker_start_failure_and_incomplete_reply_do_not_report_success(self):
        class MissingProcess(QtCore.QProcess):
            def start(self, *_):
                super().start("/no/such/deletion-worker", [])

        for fixture in (patch("gui.database_clear_dialog.QtCore.QProcess", MissingProcess),
                        self.fixture(reply={"deleted": True})):
            dialog = DeleteDatabaseDialog(self.settings)
            self.addCleanup(dialog.close)
            dialog.passwordEdit.setText(" entered secret ")
            with fixture:
                dialog.deleteButton.click()
                self.wait(dialog)
            self.assertIsNone(dialog.deleted_count)
            self.assertTrue(dialog.cancelButton.isEnabled())
            self.assertEqual(dialog._password, "")
            self.assertEqual(dialog._request, b"")
            self.assertIn("not confirmed", dialog.statusLabel.text())

    def test_success_clears_index_selection_even_when_settings_are_cancelled(self):
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        window.index_tab._groups = {("A1",): {"label": "SU(2)", "jobs": [{"theory_id": 1}]}}
        window.index_tab._render_groups()
        self.assertTrue(window.index_tab.retrieved_jobs)

        def confirmed(dialog):
            dialog.deleted_count = 1
            return QtWidgets.QDialog.DialogCode.Accepted

        def settings_interaction(dialog):
            dialog.deleteDatabaseContentsButton.click()
            return QtWidgets.QDialog.DialogCode.Rejected

        with patch.object(DeleteDatabaseDialog, "exec", confirmed), \
             patch.object(SettingsDialog, "exec", settings_interaction), \
             patch.object(QtWidgets.QMessageBox, "information"):
            window.open_settings()
        self.assertEqual(window.index_tab.retrieved_jobs, ())
        self.assertEqual(window.emptyIndexGaugeGroupsList.count(), 0)
        self.assertFalse(window.calculateIndexButton.isEnabled())

    def test_running_calculation_prevents_settings_and_deletion(self):
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        settings = SettingsDialog(self.store, window)
        self.addCleanup(settings.close)
        window.index_tab.process = object()
        try:
            with patch.object(SettingsDialog, "exec") as open_settings, \
                 patch.object(DeleteDatabaseDialog, "exec") as delete:
                window.open_settings()
                settings.deleteDatabaseContentsButton.click()
            open_settings.assert_not_called()
            delete.assert_not_called()
        finally:
            window.index_tab.process = None


if __name__ == "__main__":
    unittest.main()
