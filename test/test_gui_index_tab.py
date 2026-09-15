"""Index-tab regressions using isolated settings and real fixture processes."""

import json
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6 import QtCore, QtTest, QtWidgets
from gui.n2_db import N2DatabaseWindow, SettingsStore, default_settings


def record(theory_id, factors=("A1",), label="SU(2)"):
    data = {"hypermultiplets": [{"representation": "fundamental", "number": 4}]}
    if len(factors) == 1:
        data["algebra"] = factors[0]
    else:
        data["gauge_groups"] = [{"id": str(i), "algebra": a} for i, a in enumerate(factors)]
    return {"group": {"algebras": list(factors), "label": label}, "job": {
        "theory_id": theory_id, "lagrangian_realization_id": theory_id + 10,
        "input": data, "needed_fields": ["superconformal_index"],
        "unknown_precision": ["coulomb_branch_index"],
    }}


class IndexTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="n2-index-gui-")
        self.addCleanup(self.temp.cleanup)
        self.store = SettingsStore(Path(self.temp.name) / "settings.ini")
        self.settings = default_settings()
        self.settings.update({"mysql/database": "isolated_test", "mysql/password": ""})
        self.store.save(self.settings)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        with patch("gui.n2_db.PROJECT_ROOT", self.root):
            self.window = N2DatabaseWindow(self.store)
        self.addCleanup(self.cleanup_window)
        self.launches = []

    def cleanup_window(self):
        self.window.close()
        self.wait_search()

    def wait_search(self):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while self.window.index_tab.process is not None and timer.elapsed() < 10000:
            QtTest.QTest.qWait(10)
        self.assertIsNone(self.window.index_tab.process, self.window.indexCalculationLog.toPlainText())

    def fixture(self, records, *, exit_code=0, tail="", delay=0, jobs=None, wait_stop=False):
        # Split the final record across writes, omitting its trailing newline,
        # to exercise incremental parsing and the final QProcess drain.
        output = "\n".join(json.dumps(r) for r in records)
        script = f"""
import json, sys, time
request = json.loads(sys.stdin.readline())
assert request['settings']['mysql/database'] == 'isolated_test'
if {jobs!r} is None:
    assert all(key.startswith('mysql/') for key in request['settings'])
else:
    assert request['jobs'] == {jobs!r}
    assert request['settings']['index/full_max_order'] == 18
if {wait_stop!r}:
    assert sys.stdin.readline().strip() == 'stop'
time.sleep({delay!r})
output = {output!r}
sys.stdout.write(output[:len(output)//2]); sys.stdout.flush()
time.sleep(0.02)
sys.stdout.write(output[len(output)//2:]); sys.stdout.flush()
sys.stderr.write({tail!r}); sys.stderr.flush()
sys.exit({exit_code!r})
"""
        launches = self.launches

        class FixtureProcess(QtCore.QProcess):
            def start(self, program, arguments):
                launches.append((program, arguments))
                super().start(sys.executable, ["-B", "-u", "-c", script])

        return patch("gui.index_tab.QtCore.QProcess", FixtureProcess)

    def search(self, records, **kwargs):
        with self.fixture(records, **kwargs):
            self.window.searchEmptyIndicesButton.click()
            self.wait_search()

    def test_group_selection_and_retained_jobs_without_another_search(self):
        records = [record(1), record(2), record(3, ("A1", "C2"), "SU(2) × Sp(2)")]
        with self.fixture(records + [{"complete": 3}], delay=0.05):
            self.window.searchEmptyIndicesButton.click()
            log_path = self.window.index_tab.logger.path
            stream = self.window.index_tab.logger._stream
            self.assertEqual(log_path.parent, self.root / "logs")
            self.assertRegex(log_path.name, r"^log_index_\d{8}_\d{6}_\d{6}\.log$")
            self.assertFalse(stream.closed)
            self.assertIn("Starting search", log_path.read_text(encoding="utf-8"))
            self.assertFalse(self.window.searchEmptyIndicesButton.isEnabled())
            self.assertFalse(self.window.buildTheoriesButton.isEnabled())
            self.assertFalse(self.window.actionSettings.isEnabled())
            self.window.anomaly_tab.build_theories()
            self.assertIsNone(self.window.anomaly_tab.process)
            self.assertIsNone(self.window.anomaly_tab.logger.path)
            # A GUI timer fires while the worker is still pending.
            ticks = []
            QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
            self.wait_search()
            self.assertEqual(ticks, [True])
        controller = self.window.index_tab
        self.assertTrue(stream.closed)
        self.assertIsNone(controller.logger._stream)
        self.assertEqual(log_path.read_text(encoding="utf-8"),
                         self.window.indexCalculationLog.toPlainText() + "\n")
        listing = self.window.emptyIndexGaugeGroupsList
        self.assertEqual(listing.count(), 2)
        self.assertIn("2 theories", listing.item(0).text())
        self.assertIn("SU(2) × Sp(2)", listing.item(1).text())
        self.assertEqual(controller.selected_jobs, ())
        listing.item(1).setCheckState(QtCore.Qt.CheckState.Checked)
        self.assertEqual([job["theory_id"] for job in controller.selected_jobs], [3])
        self.assertFalse(self.window.selectAllIndexGroupsCheckBox.isChecked())
        self.window.selectAllIndexGroupsCheckBox.click()
        self.assertEqual(len(controller.selected_jobs), 3)
        listing.item(0).setCheckState(QtCore.Qt.CheckState.Unchecked)
        self.assertFalse(self.window.selectAllIndexGroupsCheckBox.isChecked())
        # Manual checking of the last missing group updates Select all without
        # its toggled signal clearing the other group's selection.
        listing.item(0).setCheckState(QtCore.Qt.CheckState.Checked)
        self.assertTrue(self.window.selectAllIndexGroupsCheckBox.isChecked())
        self.window.selectAllIndexGroupsCheckBox.click()
        self.assertEqual(controller.selected_jobs, ())
        self.assertEqual(controller.retrieved_jobs, tuple(r["job"] for r in records))
        copied = controller.retrieved_jobs
        copied[0]["input"].clear()
        self.assertEqual(controller.retrieved_jobs[0], records[0]["job"])
        self.assertEqual(controller.database["database"], "isolated_test")
        self.assertNotIn("password", controller.database)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0][1], ["-python", "-B", "-u", "-m", "gui.index_search"])
        self.assertTrue(self.window.buildTheoriesButton.isEnabled())
        self.assertTrue(self.window.actionSettings.isEnabled())
        self.assertFalse(self.window.calculateIndexButton.isEnabled())

    def test_refresh_failure_partial_response_and_empty_success(self):
        self.search([record(1), {"complete": 1}])
        first_path = self.window.index_tab.logger.path
        first_log = first_path.read_text(encoding="utf-8")
        self.window.selectAllIndexGroupsCheckBox.click()
        for records, code in (([record(2)], 1), ([record(2)], 0),
                              ([record(2), {"complete": 9}], 0),
                              ([record(2), record(2), {"complete": 2}], 0)):
            with self.subTest(records=records, code=code):
                self.search(records, exit_code=code)
                self.assertEqual([j["theory_id"] for j in self.window.index_tab.selected_jobs], [1])
                self.assertIsNone(self.window.index_tab.logger._stream)
                self.assertIn("Search failed", self.window.index_tab.logger.path.read_text(encoding="utf-8"))
        self.search([record(2), {"complete": 1}])
        self.assertEqual([j["theory_id"] for j in self.window.index_tab.selected_jobs], [2])
        self.search([{"complete": 0}])
        self.assertEqual(self.window.index_tab.retrieved_jobs, ())
        self.assertEqual(self.window.emptyIndexGaugeGroupsList.count(), 0)
        self.assertFalse(self.window.selectAllIndexGroupsCheckBox.isEnabled())
        self.assertIn("Found 0 theories", self.window.indexCalculationLog.toPlainText())
        self.assertIn("Found 0 theories", self.window.index_tab.logger.path.read_text(encoding="utf-8"))
        self.assertEqual(first_path.read_text(encoding="utf-8"), first_log)
        self.assertEqual(len(list((self.root / "logs").glob("log_index_*.log"))), 7)

    def test_group_checkboxes_support_mouse_and_keyboard(self):
        self.window.tabs.setCurrentWidget(self.window.indexTab)
        self.window.show()
        self.search([record(1), record(2, ("A2",), "SU(3)"), {"complete": 2}])
        listing = self.window.emptyIndexGaugeGroupsList
        rect = listing.visualItemRect(listing.item(0))
        QtTest.QTest.mouseClick(listing.viewport(), QtCore.Qt.MouseButton.LeftButton,
                               pos=rect.topLeft() + QtCore.QPoint(9, rect.height() // 2))
        self.assertEqual(len(self.window.index_tab.selected_jobs), 1)
        QtTest.QTest.keyClick(listing, QtCore.Qt.Key.Key_Space)
        self.assertEqual(self.window.index_tab.selected_jobs, ())
        QtTest.QTest.keyClick(listing, QtCore.Qt.Key.Key_Down)
        QtTest.QTest.keyClick(listing, QtCore.Qt.Key.Key_Space)
        self.assertEqual([job["theory_id"] for job in self.window.index_tab.selected_jobs], [2])

    def test_errors_are_redacted_and_failed_start_restores_controls(self):
        self.settings["mysql/password"] = "private fixture secret"
        with patch.object(self.store, "load", return_value=self.settings):
            self.search([{"log": "Failed: private fixture secret\nsecond line", "level": "ERROR",
                          "timestamp": "2026-09-14 10:20:30.456-03:30"}],
                        exit_code=1, tail="private fixture secret on stderr")
        log = self.window.indexCalculationLog.toPlainText()
        self.assertNotIn("private fixture secret", log)
        self.assertIn("[ERROR] Failed: [redacted]", log)
        self.assertIn("[ERROR] second line", log)
        stamp = datetime.fromisoformat("2026-09-14 10:20:30.456-03:30").astimezone().isoformat(
            sep=" ", timespec="milliseconds",
        )
        self.assertIn(f"[{stamp}] [ERROR] second line", log)
        self.assertIn("[WARNING] [redacted] on stderr", log)
        self.assertEqual(self.window.index_tab.logger._secrets, ())
        saved = self.window.index_tab.logger.path.read_text(encoding="utf-8")
        self.assertEqual(saved, log + "\n")
        self.assertNotIn("private fixture secret", saved)
        self.assertNotIn("private fixture secret", repr(self.launches))

        class MissingProcess(QtCore.QProcess):
            def start(self, program, arguments):
                super().start("/missing/n2-search-executable", [])

        with patch("gui.index_tab.QtCore.QProcess", MissingProcess):
            self.window.searchEmptyIndicesButton.click()
            self.wait_search()
        self.assertTrue(self.window.searchEmptyIndicesButton.isEnabled())
        self.assertTrue(self.window.actionSettings.isEnabled())
        self.assertIsNone(self.window.index_tab.logger._stream)
        self.assertIn("Search process:", self.window.index_tab.logger.path.read_text(encoding="utf-8"))

    def test_database_change_invalidates_snapshot_but_cutoff_change_does_not(self):
        self.search([record(1), {"complete": 1}])
        changed = dict(self.settings, **{"index/full_max_order": 24})
        self.window.index_tab.invalidate_if_database_changed(changed)
        self.assertEqual(len(self.window.index_tab.retrieved_jobs), 1)
        changed["mysql/database"] = "another_database"
        self.window.index_tab.invalidate_if_database_changed(changed)
        self.assertEqual(self.window.index_tab.retrieved_jobs, ())
        self.assertIsNone(self.window.index_tab.database)
        self.assertEqual(self.window.emptyIndexGaugeGroupsList.count(), 0)

    def test_missing_database_and_active_build_do_not_launch_search(self):
        with patch("gui.index_tab.QtCore.QProcess") as process:
            self.window.anomaly_tab.process = object()
            self.window._sync_tab_availability()
            self.window.index_tab.search()
            self.assertFalse((self.root / "logs").exists())
            self.window.anomaly_tab.process = None
            self.window._sync_tab_availability()
            self.settings["mysql/database"] = ""
            with patch.object(self.store, "load", return_value=self.settings):
                self.window.searchEmptyIndicesButton.click()
        process.assert_not_called()
        self.assertIn("Set the MySQL database name", self.window.indexCalculationLog.toPlainText())
        self.assertIn("Set the MySQL database name", self.window.index_tab.logger.path.read_text(encoding="utf-8"))
        self.assertIsNone(self.window.index_tab.logger._stream)

    def test_settings_failure_is_saved_and_log_file_failure_does_not_block_search(self):
        with patch.object(self.store, "load", side_effect=OSError("Cannot read preferences")):
            self.window.searchEmptyIndicesButton.click()
        self.assertIn("Cannot read preferences", self.window.index_tab.logger.path.read_text(encoding="utf-8"))
        self.assertIsNone(self.window.index_tab.logger._stream)
        # A regular file prevents creating a logs directory. Search must still
        # complete with its results and an explanation in the GUI.
        other_root = self.root / "blocked"
        other_root.mkdir()
        blocker = other_root / "logs"
        blocker.write_text("existing file", encoding="utf-8")
        self.window.index_tab.project_root = other_root
        self.search([record(1), {"complete": 1}])
        self.assertEqual(len(self.window.index_tab.retrieved_jobs), 1)
        self.assertIn("Cannot create index search log", self.window.indexCalculationLog.toPlainText())
        self.assertEqual(blocker.read_text(encoding="utf-8"), "existing file")
        self.assertIsNone(self.window.index_tab.logger._stream)

    def test_close_cancels_search_without_publishing_partial_results(self):
        self.window.show()
        with self.fixture([record(1), {"complete": 1}], delay=20):
            self.window.searchEmptyIndicesButton.click()
            self.window.close()
            self.wait_search()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.window.index_tab.retrieved_jobs, ())
        self.assertIsNone(self.window.index_tab.logger._stream)
        self.assertIn("cancelling the database search", self.window.index_tab.logger.path.read_text(encoding="utf-8"))

    def test_calculation_uses_selected_snapshot_and_keeps_failed_components(self):
        self.search([record(1), record(2), record(3, ("A2",), "SU(3)"), {"complete": 3}])
        self.window.emptyIndexGaugeGroupsList.item(0).setCheckState(QtCore.Qt.CheckState.Checked)
        jobs = list(self.window.index_tab.selected_jobs)
        records = [
            {"log": "Theory 1: full index saved."},
            {"result": {"theory_id": 1, "lagrangian_realization_id": 11, "remaining_fields": []}},
            {"result": {"theory_id": 2, "lagrangian_realization_id": 12,
                        "remaining_fields": ["superconformal_index"]}},
            {"calculation_complete": {"status": "completed with errors", "processed": 2, "failed": 1, "total": 2}},
        ]
        with self.fixture(records, exit_code=1, jobs=jobs, delay=0.05):
            self.window.calculateIndexButton.click()
            self.assertEqual(self.window.calculateIndexButton.text(), "Stop")
            self.assertFalse(self.window.searchEmptyIndicesButton.isEnabled())
            self.assertFalse(self.window.emptyIndexGaugeGroupsList.isEnabled())
            self.assertFalse(self.window.selectAllIndexGroupsCheckBox.isEnabled())
            self.assertFalse(self.window.actionSettings.isEnabled())
            self.assertFalse(self.window.buildTheoriesButton.isEnabled())
            self.wait_search()
        self.assertEqual([j["theory_id"] for j in self.window.index_tab.retrieved_jobs], [2, 3])
        self.assertEqual([j["theory_id"] for j in self.window.index_tab.selected_jobs], [2])
        self.assertEqual(self.launches[-1][1][-1], "gui.index_calculator")
        self.assertEqual(len(self.launches), 2)  # No second empty-theory search.
        self.assertEqual(self.window.calculateIndexButton.text(), "Calculate index")
        self.assertTrue(self.window.calculateIndexButton.isEnabled())
        self.assertIsNone(self.window.index_tab.logger._stream)
        self.assertIn("full index saved", self.window.index_tab.logger.path.read_text())
        self.assertEqual(len(list((self.root / "logs").glob("log_index_*.log"))), 2)

    def test_incomplete_calculation_keeps_unknown_jobs_but_accepts_saved_results(self):
        self.search([record(1), record(2), {"complete": 2}])
        self.window.selectAllIndexGroupsCheckBox.click()
        jobs = list(self.window.index_tab.selected_jobs)
        with self.fixture([{"result": {"theory_id": 1, "lagrangian_realization_id": 11,
                                       "remaining_fields": []}}], exit_code=1, jobs=jobs):
            self.window.calculateIndexButton.click()
            self.wait_search()
        self.assertEqual([j["theory_id"] for j in self.window.index_tab.selected_jobs], [2])
        self.assertIn("without a complete report", self.window.indexCalculationLog.toPlainText())

    def test_calculation_close_requests_stop_and_preserves_pending_work(self):
        self.search([record(1), {"complete": 1}])
        self.window.selectAllIndexGroupsCheckBox.click()
        jobs = list(self.window.index_tab.selected_jobs)
        self.window.show()
        with self.fixture([{"calculation_complete": {
            "status": "stopped", "processed": 0, "failed": 0, "total": 1,
        }}], jobs=jobs, wait_stop=True):
            self.window.calculateIndexButton.click()
            self.window.close()  # Includes Stop while the process is starting.
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window.calculateIndexButton.isEnabled())
            self.wait_search()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(list(self.window.index_tab.selected_jobs), jobs)
        self.assertIn("Stop requested", self.window.index_tab.logger.path.read_text())

    def test_no_selection_changed_database_and_active_build_prevent_calculation(self):
        self.search([record(1), {"complete": 1}])
        self.assertFalse(self.window.calculateIndexButton.isEnabled())
        self.window.selectAllIndexGroupsCheckBox.click()
        self.window.index_tab.set_available(False)
        with patch("gui.index_tab.QtCore.QProcess") as process:
            self.window.index_tab.calculate()
            self.window.index_tab.set_available(True)
            settings = dict(self.settings, **{"mysql/database": "changed_test"})
            with patch.object(self.store, "load", return_value=settings):
                self.window.index_tab.calculate()
            process.assert_not_called()
        self.assertEqual(self.window.index_tab.retrieved_jobs, ())

    def test_success_removes_finished_group_and_disables_calculate(self):
        self.search([record(1), {"complete": 1}])
        self.window.selectAllIndexGroupsCheckBox.click()
        jobs = list(self.window.index_tab.selected_jobs)
        with self.fixture([
            {"result": {"theory_id": 1, "lagrangian_realization_id": 11, "remaining_fields": []}},
            {"calculation_complete": {"status": "completed", "processed": 1, "total": 1, "failed": 0}},
        ], jobs=jobs):
            self.window.calculateIndexButton.click()
            self.wait_search()
        self.assertEqual(self.window.index_tab.retrieved_jobs, ())
        self.assertEqual(self.window.emptyIndexGaugeGroupsList.count(), 0)
        self.assertFalse(self.window.calculateIndexButton.isEnabled())
        self.assertFalse(self.window.selectAllIndexGroupsCheckBox.isEnabled())


if __name__ == "__main__":
    unittest.main()
