"""Isolated GUI checks; no real credentials, databases or user settings are used."""

import ast
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtTest, QtWidgets

# Support unittest discovery and direct execution from test/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui.n2_db import (
    N2DatabaseWindow, PROJECT_ROOT, SettingsDialog, SettingsStore, default_settings,
)
from gui.password_store import PasswordStorageError, PasswordVault


class MemoryVault:
    """Test double only: never accesses the user's actual credential vault."""

    def __init__(self):
        self.records = {}

    def get(self, credential_id):
        try:
            return self.records[credential_id]
        except KeyError:
            raise PasswordStorageError("Test credential missing") from None

    def set(self, credential_id, password):
        self.records[credential_id] = password

    def delete(self, credential_id):
        self.records.pop(credential_id, None)


def keyword_defaults(relative_path, function_name):
    """Read real backend defaults without importing Sage or opening a cache."""
    tree = ast.parse((PROJECT_ROOT / relative_path).read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return {
        arg.arg: ast.literal_eval(value)
        for arg, value in zip(function.args.kwonlyargs, function.args.kw_defaults)
        if isinstance(value, ast.Constant)
    }


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="n2-gui-test-")
        self.addCleanup(self.temp.cleanup)
        self.vault = MemoryVault()
        self.store = SettingsStore(
            Path(self.temp.name) / "config" / "settings.ini", vault=self.vault,
        )
        self.env = patch.dict(os.environ, {
            "N2_DB_HOST": "127.0.0.1", "N2_DB_USER": "root", "N2_DB_PASSWORD": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def dialog(self):
        dialog = SettingsDialog(self.store)
        self.addCleanup(dialog.close)
        return dialog

    def wait_for_build(self, window):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while window.anomaly_tab.process is not None and timer.elapsed() < 10000:
            QtTest.QTest.qWait(10)
        self.assertIsNone(window.anomaly_tab.process, window.theoryBuildLog.toPlainText())

    def isolate_build_logs(self):
        root = Path(self.temp.name) / "project"
        root.mkdir()
        override = patch("gui.n2_db.PROJECT_ROOT", root)
        override.start()
        self.addCleanup(override.stop)
        return root

    def test_build_uses_saved_settings_and_streams_process_errors(self):
        self.isolate_build_logs()
        settings = default_settings()
        settings.update({"mysql/database": "gui_test", "mysql/password": "private dummy",
                         "index/full_max_order": 21, "cache/character_database": "/tmp/custom chars.db",
                         "tools/processes": 3})
        self.store.save(settings)
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        window.theoriesInput.setPlainText("A1\nA1, C2")
        window.buildCharacterCacheCheckBox.setChecked(True)
        launches = []
        script = """
import json, sys
r = json.loads(sys.stdin.readline())
assert r['text'] == 'A1\\nA1, C2'
assert r['build_cache'] is True
assert r['settings']['mysql/database'] == 'gui_test'
assert r['settings']['index/full_max_order'] == 21
assert r['settings']['cache/character_database'] == '/tmp/custom chars.db'
assert r['settings']['tools/processes'] == 3
print(json.dumps({'log': 'Working on A1: candidates=2', 'level': 'INFO',
                  'timestamp': '2026-09-13 12:34:56.789+09:00'}), flush=True)
print(json.dumps({'log': 'Candidate rejected', 'level': 'WARNING'}), flush=True)
print(json.dumps({'log': 'Database unavailable', 'level': 'ERROR'}), flush=True)
print('Error reason: ' + r['settings']['mysql/password'], file=sys.stderr, flush=True)
sys.exit(1)
"""

        class FixtureProcess(QtCore.QProcess):
            def start(self, program, arguments):
                launches.append((program, arguments))
                super().start(sys.executable, ["-B", "-u", "-c", script])

        with patch("gui.anomaly_tab.QtCore.QProcess", FixtureProcess):
            window.buildTheoriesButton.click()
            self.assertEqual(window.buildTheoriesButton.text(), "Stop")
            self.assertTrue(window.theoriesInput.isReadOnly())
            self.assertFalse(window.actionSettings.isEnabled())
            self.assertFalse(window.searchEmptyIndicesButton.isEnabled())
            window.index_tab.search()
            self.assertIsNone(window.index_tab.process)
            self.wait_for_build(window)
        log = window.theoryBuildLog.toPlainText()
        self.assertIn("Working on A1", log)
        timestamp = datetime.fromisoformat("2026-09-13 12:34:56.789+09:00").astimezone().isoformat(
            sep=" ", timespec="milliseconds",
        )
        self.assertIn(f"[{timestamp}] [INFO] Working on A1: candidates=2", log)
        self.assertIn("[WARNING] Candidate rejected", log)
        self.assertIn("[ERROR] Database unavailable", log)
        self.assertIn("[WARNING] Error reason: [redacted]", log)
        self.assertIn("Error reason: [redacted]", log)
        self.assertIn("exited with code 1", log)
        self.assertNotIn("private dummy", log)
        self.assertNotIn("private dummy", repr(launches))
        self.assertEqual(launches[0][1], ["-python", "-B", "-u", "-m", "gui.theory_builder"])
        self.assertEqual(window.buildTheoriesButton.text(), "Build")
        self.assertFalse(window.theoriesInput.isReadOnly())
        self.assertTrue(window.actionSettings.isEnabled())
        self.assertTrue(window.searchEmptyIndicesButton.isEnabled())
        self.assertIsNone(window.anomaly_tab.logger._stream)
        saved = window.anomaly_tab.logger.path.read_text(encoding="utf-8")
        self.assertEqual(saved, log + "\n")
        self.assertNotIn("private dummy", saved)

    def test_build_stop_and_close_wait_for_worker(self):
        root = self.isolate_build_logs()
        settings = default_settings()
        settings["mysql/database"] = "gui_test"
        self.store.save(settings)
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        window.theoriesInput.setPlainText("A1")
        script = """
import json, sys
json.loads(sys.stdin.readline())
assert sys.stdin.readline().strip() == 'stop'
print(json.dumps({'log': 'Build stopped; writes kept.'}), flush=True)
"""

        class FixtureProcess(QtCore.QProcess):
            def start(self, program, arguments):
                super().start(sys.executable, ["-B", "-u", "-c", script])

        with patch("gui.anomaly_tab.QtCore.QProcess", FixtureProcess):
            window.buildTheoriesButton.click()
            window.buildTheoriesButton.click()  # Stop even before the started signal.
            self.assertFalse(window.buildTheoriesButton.isEnabled())
            self.wait_for_build(window)
            window.show()
            window.buildTheoriesButton.click()
            window.close()
            self.assertIsNotNone(window.anomaly_tab.process)
            self.wait_for_build(window)
        self.assertFalse(window.isVisible())
        self.assertIn("Build stopped; writes kept.", window.theoryBuildLog.toPlainText())
        files = list((root / "logs").glob("log_anomalies_*.log"))
        self.assertEqual(len(files), 2)
        for path in files:
            saved = path.read_text(encoding="utf-8")
            self.assertIn("Stop requested", saved)
            self.assertIn("Build stopped; writes kept.", saved)

    def test_build_startup_failures_are_logged_and_controls_recover(self):
        root = self.isolate_build_logs()
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        window.buildTheoriesButton.click()
        self.assertIn("at least one gauge group", window.theoryBuildLog.toPlainText())
        window.theoriesInput.setPlainText("A1")
        window.buildTheoriesButton.click()
        self.assertIn("MySQL database name", window.theoryBuildLog.toPlainText())
        with patch.object(self.store, "load", side_effect=PasswordStorageError("Vault locked")):
            window.buildTheoriesButton.click()
        self.assertIn("Vault locked", window.theoryBuildLog.toPlainText())
        settings = default_settings()
        settings["mysql/database"] = "gui_test"
        self.store.save(settings)

        class MissingProcess(QtCore.QProcess):
            def start(self, program, arguments):
                super().start("/nonexistent/n2-build-worker", [])

        with patch("gui.anomaly_tab.QtCore.QProcess", MissingProcess):
            window.buildTheoriesButton.click()
            self.wait_for_build(window)
        self.assertTrue(window.buildTheoriesButton.isEnabled())
        self.assertIn("[ERROR] Build process", window.theoryBuildLog.toPlainText())
        self.assertIsNone(window.anomaly_tab.logger._stream)
        files = sorted((root / "logs").glob("log_anomalies_*.log"))
        self.assertEqual(len(files), 4)
        for path, reason in zip(files, ("at least one gauge group", "MySQL database name",
                                        "Vault locked", "[ERROR] Build process")):
            self.assertIn(reason, path.read_text(encoding="utf-8"))

    def test_anomaly_log_flushes_utf8_and_preserves_colliding_files(self):
        root = self.isolate_build_logs()
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        self.assertFalse((root / "logs").exists())
        with patch("gui.logging_utils.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 13, 12, 34, 56, 789123)
            window.anomaly_tab.start_log()
            first = window.anomaly_tab.logger.path
            self.assertEqual(first.name, "log_anomalies_20260913_123456_789123.log")
            stream = window.anomaly_tab.logger._stream
            window.anomaly_tab.logger.set_secrets("dummy secret")
            window.anomaly_tab.logger.log("진행: A1 — dummy secret\nvalid SCFTs=2")
            expected = window.theoryBuildLog.toPlainText() + "\n"
            stamp = clock.now.return_value.astimezone().isoformat(sep=" ", timespec="milliseconds")
            self.assertIn(f"[{stamp}] [INFO] 진행: A1 — [redacted]\n"
                          f"[{stamp}] [INFO] valid SCFTs=2", expected)
            # Read while the stream is still open: messages must already be flushed.
            self.assertEqual(first.read_text(encoding="utf-8"), expected)
            self.assertNotIn("dummy secret", expected)
            window.anomaly_tab.start_log()
        self.assertTrue(stream.closed)
        second = window.anomaly_tab.logger.path
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), expected)
        second_stream = window.anomaly_tab.logger._stream
        window.anomaly_tab.logger.log("Second attempt")
        window.close()
        self.assertTrue(second_stream.closed)
        self.assertIn("Second attempt", second.read_text(encoding="utf-8"))
        self.assertNotIn("Second attempt", first.read_text(encoding="utf-8"))

    def test_anomaly_log_io_failures_keep_window_logging_available(self):
        root = self.isolate_build_logs()
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        # A regular file blocks creation of the required logs directory.
        blocker = root / "logs"
        blocker.write_text("existing file", encoding="utf-8")
        window.buildTheoriesButton.click()
        self.assertIn("Cannot create anomaly log", window.theoryBuildLog.toPlainText())
        self.assertIn("at least one gauge group", window.theoryBuildLog.toPlainText())
        self.assertEqual(blocker.read_text(encoding="utf-8"), "existing file")
        blocker.unlink()
        window.anomaly_tab.start_log()
        stream = window.anomaly_tab.logger._stream
        with patch.object(stream, "write", side_effect=OSError("disk full")):
            window.anomaly_tab.logger.log("Build progress")
        self.assertTrue(stream.closed)
        self.assertIsNone(window.anomaly_tab.logger._stream)
        window.anomaly_tab.logger.log("Still working")
        log = window.theoryBuildLog.toPlainText()
        self.assertIn("Build progress", log)
        self.assertIn("Cannot write anomaly log", log)
        self.assertIn("disk full", log)
        self.assertIn("Still working", log)
        self.assertIn("[ERROR] Cannot write anomaly log", log)

    def test_worker_emits_timestamp_and_error_level_for_startup_failure(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "gui.theory_builder"],
            input=json.dumps({"text": "", "settings": {}, "build_cache": False}) + "\n",
            text=True, capture_output=True, cwd=PROJECT_ROOT, timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record["level"], "ERROR")
            self.assertIsNotNone(datetime.fromisoformat(record["timestamp"]).utcoffset())
        self.assertIn("at least one gauge group", records[0]["log"])

    def test_defaults_match_current_backend(self):
        values = default_settings()
        mysql = keyword_defaults("common/n2_theory_db.py", "connect_database")
        index = keyword_defaults("index/n2_theory_index.py", "calculate_index")
        for field in ("host", "port", "user", "password", "connect_timeout"):
            self.assertEqual(values[f"mysql/{field}"], mysql[field])
        for field in ("lie_executable", "form_executable", "tform_executable", "form_threads", "timeout"):
            self.assertEqual(values[f"tools/{field}"], index[field])
        tree = ast.parse((PROJECT_ROOT / "common/n2_theory_properties.py").read_text())
        cutoffs = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ("INDEX_MAX_ORDER", "C_INDEX_MAX_ORDER")
        }
        self.assertEqual(values["index/full_max_order"], cutoffs["INDEX_MAX_ORDER"])
        self.assertEqual(values["index/coulomb_max_dimension"], str(cutoffs["C_INDEX_MAX_ORDER"]))
        dialog = self.dialog()
        self.assertEqual(dialog.fullIndexOrderSpin.value(), cutoffs["INDEX_MAX_ORDER"])
        self.assertEqual(dialog.coulombMaxDimensionEdit.text(), str(cutoffs["C_INDEX_MAX_ORDER"]))
        self.assertEqual(values["tools/processes"], -1)
        self.assertEqual(dialog.coresSpin.value(), -1)
        self.assertEqual(dialog.formThreadsSpin.value(), 1)
        self.assertEqual(dialog.formThreadsSpin.minimum(), 1)
        self.assertEqual(dialog.tformEdit.text(), "tform")
        self.assertEqual(values["mysql/database"], "")
        for key, module, constant in (
            ("cache/character_database", "char_decomposition_cache", "DEFAULT_CHAR_CACHE_DATABASE"),
            ("cache/form_database", "form_expansion_cache", "DEFAULT_FORM_CACHE_DATABASE"),
        ):
            tree = ast.parse((PROJECT_ROOT / "index" / f"{module}.py").read_text())
            assignment = next(
                node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == constant
                        for target in node.targets)
            )
            filename = ast.literal_eval(assignment.value.right)
            self.assertEqual(values[key], str(PROJECT_ROOT / filename))
        self.assertFalse(self.store.path.exists())

    def test_anomaly_layout_and_menu_opens_settings(self):
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        self.assertEqual([window.tabs.tabText(i) for i in range(3)], ["anomaly", "index", "search"])
        self.assertEqual(window.tabs.count(), 3)
        self.assertEqual(window.indexSplitter.count(), 2)
        self.assertEqual(window.emptyIndexGaugeGroupsList.count(), 0)
        self.assertTrue(window.indexCalculationLog.isReadOnly())
        self.assertFalse(window.theoriesInput.isReadOnly())
        self.assertTrue(window.theoryBuildLog.isReadOnly())
        observed = []

        def close_dialog():
            active = self.app.activeModalWidget()
            observed.append(type(active))
            if active:
                active.reject()

        QtCore.QTimer.singleShot(0, close_dialog)
        window.actionSettings.trigger()
        self.assertEqual(observed, [SettingsDialog])
        self.assertFalse(self.store.path.exists())

    def test_load_theories_appends_files_and_can_undo_without_losing_edits(self):
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        window.theoriesInput.insertPlainText("A1")
        # A selection in the existing text must not be replaced by the load.
        window.theoriesInput.selectAll()
        first = Path(self.temp.name) / "이론 1.txt"
        first.write_bytes(b"\xef\xbb\xbfA2\r\nA1, C2")
        second = Path(self.temp.name) / "theories 2.txt"
        second.write_text("E6\n\nG2\n", encoding="utf-8")
        with patch.object(QtWidgets.QFileDialog, "getOpenFileNames",
                          return_value=([str(first), str(second)], "")):
            window.loadTheoriesButton.click()
        expected = "A1\nA2\nA1, C2\nE6\n\nG2\n"
        self.assertEqual(window.theoriesInput.toPlainText(), expected)
        self.assertEqual(window.theoryBuildLog.toPlainText(), "")
        self.assertFalse(self.store.path.exists())
        window.theoriesInput.undo()
        self.assertEqual(window.theoriesInput.toPlainText(), "A1")
        window.theoriesInput.undo()
        self.assertEqual(window.theoriesInput.toPlainText(), "")
        window.theoriesInput.redo()
        window.theoriesInput.redo()
        self.assertEqual(window.theoriesInput.toPlainText(), expected)

    def test_load_theories_cancel_empty_files_and_line_boundaries(self):
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        empty = Path(self.temp.name) / "empty.txt"
        empty.write_bytes(b"\xef\xbb\xbf")
        theory = Path(self.temp.name) / "theory.txt"
        theory.write_text("A2\n", encoding="utf-8")
        for existing in ("", "A1", "A1\n"):
            with self.subTest(existing=existing):
                window.theoriesInput.setPlainText(existing)
                for files in ([], [str(empty)]):
                    with patch.object(QtWidgets.QFileDialog, "getOpenFileNames",
                                      return_value=(files, "")):
                        window.loadTheoriesButton.click()
                    self.assertEqual(window.theoriesInput.toPlainText(), existing)
                    self.assertFalse(window.theoriesInput.document().isUndoAvailable())
                with patch.object(QtWidgets.QFileDialog, "getOpenFileNames",
                                  return_value=([str(empty), str(theory), str(empty)], "")):
                    window.loadTheoriesButton.click()
                expected = "A2\n" if not existing else "A1\nA2\n"
                self.assertEqual(window.theoriesInput.toPlainText(), expected)

    def test_failed_theory_load_keeps_all_existing_text_and_undo_history(self):
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        valid = Path(self.temp.name) / "valid.txt"
        valid.write_text("A2", encoding="utf-8")
        invalid = Path(self.temp.name) / "invalid.txt"
        invalid.write_bytes(b"\xff\xfe")
        for bad_file in (invalid, Path(self.temp.name) / "missing.txt"):
            with self.subTest(bad_file=bad_file):
                window.theoriesInput.insertPlainText("A1, C2")
                with patch.object(QtWidgets.QFileDialog, "getOpenFileNames",
                                  return_value=([str(valid), str(bad_file)], "")):
                    with patch.object(QtWidgets.QMessageBox, "critical") as error:
                        window.loadTheoriesButton.click()
                error.assert_called_once()
                self.assertIn(str(bad_file), error.call_args.args[2])
                self.assertEqual(window.theoriesInput.toPlainText(), "A1, C2")
                window.theoriesInput.undo()
                self.assertEqual(window.theoriesInput.toPlainText(), "")

    def test_save_then_relaunch_in_fresh_process(self):
        window = N2DatabaseWindow(self.store)
        dialog = self.dialog()
        values = {
            "cache/character_database": str(Path(self.temp.name) / "캐시 chars.db"),
            "cache/form_database": str(Path(self.temp.name) / "FORM cache.db"),
            "index/full_max_order": 24, "index/coulomb_max_dimension": "101/3",
            "mysql/database": "landscape_test", "mysql/host": "db.example.invalid",
            "mysql/port": 3307, "mysql/user": "researcher",
            "mysql/password": " fake secret = ; # 한글 ", "mysql/connect_timeout": 17,
            "tools/lie_executable": "/example tools/lie",
            "tools/form_executable": "/example tools/form", "tools/timeout": 123.75,
            "tools/tform_executable": "/example tools/tform", "tools/form_threads": 2,
            "tools/processes": 3,
        }
        for key, field in dialog.text_fields.items():
            field.setText(values[key])
        for key, field in dialog.number_fields.items():
            field.setValue(values[key])
        self.assertEqual(dialog.passwordEdit.echoMode(), QtWidgets.QLineEdit.EchoMode.Password)
        dialog.buttonBox.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).click()
        self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
        window.close()
        script = """
import json, sys
sys.path.insert(0, sys.argv[1])
from PyQt6 import QtWidgets
from gui.n2_db import N2DatabaseWindow, SettingsDialog, SettingsStore
from test.test_gui_n2_db import MemoryVault
app = QtWidgets.QApplication([])
fixture = json.load(sys.stdin)
vault = MemoryVault()
vault.records = fixture['vault']
window = N2DatabaseWindow(SettingsStore(sys.argv[2], vault=vault))
expected = fixture['settings']
assert window.settings == expected
dialog = SettingsDialog(window.store)
for key, field in dialog.text_fields.items():
    assert field.text() == expected[key], key
for key, field in dialog.number_fields.items():
    assert field.value() == expected[key], key
dialog.close()
window.close()
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(PROJECT_ROOT), str(self.store.path)],
            input=json.dumps({"settings": values, "vault": self.vault.records}),
            text=True, capture_output=True,
            cwd=self.temp.name, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertFalse(Path(values["cache/character_database"]).exists())
        self.assertFalse(Path(values["cache/form_database"]).exists())
        self.assertNotIn(values["mysql/password"].encode(), self.store.path.read_bytes())
        self.assertFalse(self.store._read_preferences().contains("mysql/password"))

    def test_cancel_discards_edits(self):
        self.store.save(default_settings())
        original = self.store.path.read_bytes()
        dialog = self.dialog()
        dialog.databaseEdit.setText("discard_me")
        dialog.passwordEdit.setText("also discarded")
        dialog.fullIndexOrderSpin.setValue(30)
        dialog.coulombMaxDimensionEdit.setText("120")
        dialog.coresSpin.setValue(2)
        dialog.buttonBox.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).click()
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(self.store.load()["mysql/database"], "")

    def test_legacy_settings_use_default_cutoffs_without_rewriting(self):
        legacy = QtCore.QSettings(str(self.store.path), QtCore.QSettings.Format.IniFormat)
        legacy.setValue("mysql/database", "existing_database")
        legacy.setValue("mysql/password_id", "")
        legacy.sync()
        before = self.store.path.read_bytes()
        dialog = self.dialog()
        self.assertEqual(dialog.databaseEdit.text(), "existing_database")
        self.assertEqual(dialog.fullIndexOrderSpin.value(), 18)
        self.assertEqual(dialog.coulombMaxDimensionEdit.text(), "90")
        self.assertEqual(dialog.coresSpin.value(), -1)
        self.assertEqual(dialog.formThreadsSpin.value(), 1)
        self.assertEqual(dialog.formThreadsSpin.minimum(), 1)
        self.assertEqual(dialog.tformEdit.text(), "tform")
        dialog.reject()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_cpu_cores_reject_zero_and_save_serial_or_automatic(self):
        self.store.save(default_settings())
        before = self.store.path.read_bytes()
        dialog = self.dialog()
        dialog.coresSpin.setValue(0)
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertIn("positive integer", warning.call_args.args[2])
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
        self.assertEqual(self.store.path.read_bytes(), before)
        for value in (1, -1):
            with self.subTest(value=value):
                dialog = self.dialog()
                dialog.coresSpin.setValue(value)
                dialog.accept()
                self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
                self.assertEqual(self.store.load()["tools/processes"], value)

    def test_coulomb_cutoff_validation_and_exact_saving(self):
        self.store.save(default_settings())
        before = self.store.path.read_bytes()
        dialog = self.dialog()
        for invalid in ("", "-1", "1/0", "1.5", "nan", "1+2"):
            with self.subTest(invalid=invalid):
                dialog.coulombMaxDimensionEdit.setText(invalid)
                with patch.object(QtWidgets.QMessageBox, "warning") as warning:
                    dialog.accept()
                warning.assert_called_once()
                self.assertNotEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
                self.assertEqual(self.store.path.read_bytes(), before)
        for entered, expected in (("0", "0"), (" 12/10 ", "6/5"),
                                  ("9007199254740993/2", "9007199254740993/2")):
            with self.subTest(entered=entered):
                dialog = self.dialog()
                dialog.fullIndexOrderSpin.setValue(0)
                dialog.coulombMaxDimensionEdit.setText(entered)
                dialog.accept()
                saved = self.store.load()
                self.assertEqual(saved["index/full_max_order"], 0)
                self.assertEqual(saved["index/coulomb_max_dimension"], expected)

    def test_environment_defaults_and_saved_values_take_precedence(self):
        with patch.dict(os.environ, {
            "N2_DB_HOST": "env.invalid", "N2_DB_USER": "env_user", "N2_DB_PASSWORD": "env_dummy",
        }):
            self.assertEqual(self.store.load()["mysql/host"], "env.invalid")
            self.assertEqual(self.store.load()["mysql/user"], "env_user")
            self.assertEqual(self.store.load()["mysql/password"], "env_dummy")
        self.store.save(default_settings())
        with patch.dict(os.environ, {"N2_DB_HOST": "changed.invalid", "N2_DB_PASSWORD": "changed_dummy"}):
            self.assertEqual(self.store.load()["mysql/host"], "127.0.0.1")
            self.assertEqual(self.store.load()["mysql/password"], "")

    def test_file_pickers_and_relative_cache_paths(self):
        dialog = self.dialog()
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("custom/cache.db", "")):
            dialog.characterCacheBrowse.click()
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("/usr/bin/form", "")):
            dialog.formBrowse.click()
        self.assertEqual(dialog.characterCacheEdit.text(), "custom/cache.db")
        self.assertEqual(dialog.formEdit.text(), "/usr/bin/form")
        dialog.accept()
        self.assertEqual(self.store.load()["cache/character_database"], str(PROJECT_ROOT / "custom/cache.db"))
        self.assertFalse((PROJECT_ROOT / "custom/cache.db").exists())

    def test_missing_fields_and_write_failures_keep_dialog_open(self):
        dialog = self.dialog()
        dialog.hostEdit.clear()
        with patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertFalse(self.store.path.exists())
        dialog.hostEdit.setText("127.0.0.1")
        with patch.object(self.store, "save", side_effect=OSError("Read-only settings")):
            with patch.object(QtWidgets.QMessageBox, "critical") as error:
                dialog.accept()
        error.assert_called_once()
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
        self.assertFalse(self.store.path.exists())

    def test_plaintext_migration_preserves_password_and_removes_old_key(self):
        legacy = QtCore.QSettings(str(self.store.path), QtCore.QSettings.Format.IniFormat)
        legacy.setValue("mysql/password", " legacy dummy = # 비밀번호 ")
        legacy.setValue("mysql/database", "previous_database")
        legacy.setValue("future/setting", "preserved")
        legacy.sync()
        self.store.migrate_legacy_password()
        self.assertEqual(self.store.load()["mysql/password"], " legacy dummy = # 비밀번호 ")
        self.assertEqual(self.store.load()["mysql/database"], "previous_database")
        settings = self.store._read_preferences()
        self.assertFalse(settings.contains("mysql/password"))
        self.assertTrue(settings.value("mysql/password_id", type=str))
        self.assertEqual(settings.value("future/setting"), "preserved")
        self.assertEqual(list(self.store.path.parent.iterdir()), [self.store.path])
        self.assertNotIn(b"legacy dummy", self.store.path.read_bytes())

    def test_failed_migration_retains_original_for_retry(self):
        legacy = QtCore.QSettings(str(self.store.path), QtCore.QSettings.Format.IniFormat)
        legacy.setValue("mysql/password", "legacy test password")
        legacy.sync()
        before = self.store.path.read_bytes()
        with patch.object(self.vault, "set", side_effect=PasswordStorageError("Vault locked")):
            with self.assertRaises(PasswordStorageError):
                self.store.migrate_legacy_password()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.vault.records, {})

    def test_password_update_and_clearing_remove_previous_credentials(self):
        values = default_settings()
        values["mysql/password"] = "first dummy password"
        self.store.save(values)
        first_ids = set(self.vault.records)
        values["mysql/password"] = "second dummy password"
        self.store.save(values)
        self.assertTrue(first_ids.isdisjoint(self.vault.records))
        self.assertEqual(list(self.vault.records.values()), ["second dummy password"])
        values["mysql/password"] = ""
        self.store.save(values)
        self.assertEqual(self.vault.records, {})
        with patch.dict(os.environ, {"N2_DB_PASSWORD": "must not reappear"}):
            self.assertEqual(self.store.load()["mysql/password"], "")

    def test_failed_preferences_write_keeps_previous_password(self):
        values = default_settings()
        values["mysql/password"] = "previous dummy password"
        self.store.save(values)
        before = self.store.path.read_bytes()
        original_credentials = dict(self.vault.records)
        values["mysql/password"] = "new dummy password"
        with patch.object(self.store, "_write_preferences", side_effect=OSError("Disk failure")):
            with self.assertRaises(OSError):
                self.store.save(values)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.vault.records, original_credentials)
        self.assertEqual(self.store.load()["mysql/password"], "previous dummy password")

    def test_vault_failure_never_falls_back_to_plaintext(self):
        values = default_settings()
        values["mysql/password"] = "must not appear in files"
        with patch.object(self.vault, "set", side_effect=PasswordStorageError("Vault unavailable")):
            with self.assertRaises(PasswordStorageError):
                self.store.save(values)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.vault.records, {})

    def test_missing_or_locked_vault_does_not_replace_saved_password(self):
        values = default_settings()
        values["mysql/password"] = "saved test password"
        self.store.save(values)
        before = self.store.path.read_bytes()
        for message in ("Locked", "Missing"):
            with patch.object(self.vault, "get", side_effect=PasswordStorageError(message)):
                with patch.dict(os.environ, {"N2_DB_PASSWORD": "unsafe fallback"}):
                    with self.assertRaises(PasswordStorageError):
                        self.store.load()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_settings_profiles_have_independent_passwords(self):
        other = SettingsStore(Path(self.temp.name) / "other.ini", vault=self.vault)
        values = default_settings()
        values["mysql/password"] = "first profile dummy"
        self.store.save(values)
        values["mysql/password"] = "second profile dummy"
        other.save(values)
        self.assertEqual(self.store.load()["mysql/password"], "first profile dummy")
        self.assertEqual(other.load()["mysql/password"], "second profile dummy")

    def test_project_rename_rebases_cache_paths_without_touching_credentials(self):
        renamed_root = Path(self.temp.name) / "N2SCFTDB"
        previous_root = renamed_root.with_name("SuperconformalIndex")
        values = default_settings()
        values["cache/character_database"] = str(previous_root / "char_decomposition_cache.db")
        values["cache/form_database"] = str(previous_root / "custom" / "form.db")
        values["mysql/password"] = "rename test credential"
        self.store.save(values)
        old_credentials = dict(self.vault.records)
        original_file = self.store.path.read_bytes()
        with patch("gui.n2_db.PROJECT_ROOT", renamed_root):
            loaded = self.store.load()
        self.assertEqual(loaded["cache/character_database"], str(renamed_root / "char_decomposition_cache.db"))
        self.assertEqual(loaded["cache/form_database"], str(renamed_root / "custom" / "form.db"))
        self.assertEqual(loaded["mysql/password"], "rename test credential")
        self.assertEqual(self.vault.records, old_credentials)
        self.assertEqual(self.store.path.read_bytes(), original_file)

    def test_project_rename_preserves_external_cache_paths(self):
        values = default_settings()
        values["cache/character_database"] = "/custom/cache/characters.db"
        values["cache/form_database"] = "/custom/cache/form.db"
        self.store.save(values)
        with patch("gui.n2_db.PROJECT_ROOT", Path(self.temp.name) / "N2SCFTDB"):
            loaded = self.store.load()
        for key in ("cache/character_database", "cache/form_database"):
            self.assertEqual(loaded[key], values[key])

    def test_vault_errors_are_sanitized_and_missing_entries_raise(self):
        vault = PasswordVault()
        with patch.object(vault, "_get_backend") as backend:
            backend.return_value.set_password.side_effect = RuntimeError("secret-in-backend-error")
            with self.assertRaises(PasswordStorageError) as error:
                vault.set("test-id", "dummy")
            self.assertNotIn("secret-in-backend-error", str(error.exception))
            backend.return_value.get_password.return_value = None
            with self.assertRaises(PasswordStorageError):
                vault.get("missing-id")

    def test_vault_checks_readback_before_confirming_write(self):
        vault = PasswordVault()
        with patch.object(vault, "_get_backend") as backend:
            backend.return_value.get_password.return_value = "incorrect round trip"
            with self.assertRaises(PasswordStorageError):
                vault.set("test-id", "expected dummy password")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux native backend")
    def test_plaintext_backend_configuration_is_ignored(self):
        from keyring.backends.SecretService import Keyring
        with patch.dict(os.environ, {"PYTHON_KEYRING_BACKEND": "keyrings.alt.file.PlaintextKeyring"}):
            vault = PasswordVault()
            self.assertIsInstance(vault._get_backend(), Keyring)

    def test_vault_read_error_is_reported_by_settings_menu(self):
        window = N2DatabaseWindow(self.store)
        self.addCleanup(window.close)
        with patch.object(self.store, "load", side_effect=PasswordStorageError("Vault locked")):
            with patch.object(QtWidgets.QMessageBox, "critical") as error:
                window.actionSettings.trigger()
        error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
