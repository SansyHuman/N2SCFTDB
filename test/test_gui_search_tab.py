"""Search controller checks with real fixture subprocesses and isolated settings."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6 import QtCore, QtTest, QtWidgets
from gui.n2_db import N2DatabaseWindow, default_settings
from gui.theory_download import CSV_FIELDS


class SearchTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n2-theory-search-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = dict(default_settings(), **{'mysql/database': 'isolated_test',
                                                    'mysql/password': 'private dummy'})
        self.store = MagicMock()
        self.store.load.side_effect = lambda: dict(self.settings)
        with patch('gui.n2_db.PROJECT_ROOT', self.root):
            self.window = N2DatabaseWindow(self.store)
        self.controller = self.window.search_tab
        self.addCleanup(self.cleanup_window)
        self.launches = []

    def cleanup_window(self):
        self.window.close()
        self.wait_search()

    def wait_search(self):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while self.controller.process is not None and timer.elapsed() < 10000:
            QtTest.QTest.qWait(10)
        self.assertIsNone(self.controller.process, self.window.searchResultCountEdit.toolTip())

    def fixture(self, records, *, exit_code=0, delay=0.01, stderr='', expected=None):
        output = '\n'.join(json.dumps(record) for record in records)
        script = f'''
import json, sys, time
request = json.loads(sys.stdin.readline())
assert request['settings']['mysql/database'] == 'isolated_test'
assert all(key.startswith('mysql/') for key in request['settings'])
assert sys.stdin.read() == ''
if {expected!r} is not None:
    assert request['conditions'] == {expected!r}
time.sleep({delay!r})
output = {output!r}
sys.stdout.write(output[:len(output)//2]); sys.stdout.flush()
time.sleep(0.01)
sys.stdout.write(output[len(output)//2:]); sys.stdout.flush()
sys.stderr.write({stderr!r}); sys.stderr.flush()
sys.exit({exit_code!r})
'''
        launches = self.launches

        class FixtureProcess(QtCore.QProcess):
            def start(self, program, arguments):
                launches.append((program, arguments))
                super().start(sys.executable, ['-B', '-u', '-c', script])

        return patch('gui.search_tab.QtCore.QProcess', FixtureProcess)

    def search(self, records, **kwargs):
        with self.fixture(records, **kwargs):
            self.window.searchTheoriesButton.click()
            self.wait_search()

    def test_search_forwards_all_conditions_and_publishes_only_complete_ids(self):
        window = self.window
        window.searchGaugeGroupsEdit.setText(' A1; "C2, A1" ')
        window.searchCentralChargeAEdit.setText('1/3')
        window.searchCentralChargeCEdit.setText('0.5')
        window.searchCentralChargeAMinEdit.setText('0')
        window.searchCentralChargeAMaxEdit.setText('2')
        window.searchCentralChargeCMinEdit.setText('1/4')
        window.searchCentralChargeCMaxEdit.setText('3/4')
        window.searchTheoryIdEdit.setText('17')
        window.searchNonEmptyIndicesCheckBox.setChecked(True)
        expected = {'gauge_groups': 'A1; "C2, A1"', 'a': '1/3', 'c': '0.5', 'a_min': '0',
                    'a_max': '2', 'c_min': '1/4', 'c_max': '3/4', 'theory_id': '17',
                    'only_nonempty_indices': True}
        with self.fixture([{'theory_id': 17}, {'complete': 1}], expected=expected, delay=0.1,
                          stderr='warning private dummy\n'):
            window.searchTheoriesButton.click()
            self.assertEqual(self.controller.theory_ids, ())
            self.assertEqual(window.searchResultCountEdit.text(), 'Searching…')
            self.assertFalse(window.downloadTheoriesButton.isEnabled())
            self.assertFalse(window.searchGaugeGroupsEdit.isEnabled())
            self.assertFalse(window.buildTheoriesButton.isEnabled())
            self.assertFalse(window.searchEmptyIndicesButton.isEnabled())
            self.assertFalse(window.actionSettings.isEnabled())
            window.index_tab.search()
            window.anomaly_tab.build_theories()
            self.assertIsNone(window.index_tab.process)
            self.assertIsNone(window.anomaly_tab.process)
            ticks = []
            QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
            self.wait_search()
            self.assertTrue(ticks)
        self.assertEqual(self.controller.theory_ids, (17,))
        self.assertEqual(self.controller.conditions, expected)
        self.assertEqual(window.searchResultCountEdit.text(), '1 theory found')
        self.assertTrue(window.downloadTheoriesButton.isEnabled())
        self.assertEqual(window.downloadTheoriesButton.receivers(window.downloadTheoriesButton.clicked), 1)
        self.assertEqual(window.searchDownloadProgressBar.value(), 0)
        self.assertTrue(window.actionSettings.isEnabled())
        self.assertEqual(self.launches[0][1], ['-python', '-B', '-u', '-m', 'gui.theory_search'])
        log = self.controller.logger.path.read_text()
        self.assertIn('[redacted]', log)
        self.assertNotIn('private dummy', log)
        self.assertNotIn('password', self.controller.database)
        self.assertIsNone(self.controller.logger._stream)
        copy = self.controller.conditions
        copy['a'] = '99'
        self.assertEqual(self.controller.conditions['a'], '1/3')

    def test_empty_success_and_changed_filters_clear_results(self):
        self.search([{'theory_id': 2}, {'theory_id': 5}, {'complete': 2}])
        self.assertEqual(self.controller.theory_ids, (2, 5))
        self.window.searchCentralChargeAEdit.setText('0.25')
        self.assertEqual(self.controller.theory_ids, ())
        self.assertEqual(self.window.searchResultCountEdit.text(), '')
        self.assertFalse(self.window.downloadTheoriesButton.isEnabled())
        self.search([{'complete': 0}])
        self.assertEqual(self.window.searchResultCountEdit.text(), '0 theories found')
        self.assertIsNotNone(self.controller.database)
        self.assertFalse(self.window.downloadTheoriesButton.isEnabled())

    def test_csv_selection_does_not_change_search_conditions_or_results(self):
        self.search([{'theory_id': 1}, {'complete': 1}])
        self.window.searchCsvTheoryIdCheckBox.setChecked(False)
        self.assertEqual(self.controller.theory_ids, (1,))
        self.assertTrue(self.window.downloadTheoriesButton.isEnabled())

    def test_failures_and_invalid_protocol_discard_partial_results(self):
        cases = [([{'theory_id': 1}], 0), ([{'theory_id': 1}, {'complete': 1}], 1),
                 ([{'theory_id': 1}, {'theory_id': 1}, {'complete': 2}], 0),
                 ([{'complete': 0}, {'theory_id': 1}], 0), ([{'complete': True}], 0),
                 ([{'theory_id': True}, {'complete': 1}], 0), ([{'complete': 0}, {'complete': 0}], 0),
                 ([{'log': 'private dummy invalid query', 'level': 'ERROR'}, {'complete': 0}], 0),
                 (['not a record', {'complete': 0}], 0)]
        for records, exit_code in cases:
            with self.subTest(records=records):
                self.search(records, exit_code=exit_code)
                self.assertEqual(self.controller.theory_ids, ())
                self.assertIn('failed', self.window.searchResultCountEdit.text())
                self.assertFalse(self.window.downloadTheoriesButton.isEnabled())
                self.assertNotIn('private dummy', self.window.searchResultCountEdit.toolTip())

    def test_database_change_deletion_and_other_work_invalidate_snapshot(self):
        for mode in ('database', 'deletion', 'work'):
            with self.subTest(mode=mode):
                self.search([{'theory_id': 1}, {'complete': 1}])
                if mode == 'database':
                    self.controller.invalidate_if_database_changed(dict(self.settings, **{'mysql/database': 'other'}))
                elif mode == 'deletion':
                    self.controller.invalidate_after_database_deletion(self.settings)
                else:
                    self.controller.set_available(False)
                self.assertEqual(self.controller.theory_ids, ())
                self.assertFalse(self.window.downloadTheoriesButton.isEnabled())
                self.controller.set_available(True)

    def test_cancel_button_and_window_close_release_process(self):
        for close in (False, True):
            with self.subTest(close=close), self.fixture([{'complete': 0}], delay=10):
                self.window.searchTheoriesButton.click()
                QtTest.QTest.qWait(50)
                if close:
                    self.window.close()
                else:
                    self.window.searchTheoriesButton.click()
                self.wait_search()
                self.assertEqual(self.controller.theory_ids, ())
                self.assertEqual(self.window.searchResultCountEdit.text(), 'Search cancelled')

    def test_failed_start_restores_controls(self):
        class MissingProcess(QtCore.QProcess):
            def start(self, *_):
                super().start('/missing/n2-search-executable', [])
        with patch('gui.search_tab.QtCore.QProcess', MissingProcess):
            self.window.searchTheoriesButton.click()
            self.wait_search()
        self.assertIn('failed', self.window.searchResultCountEdit.text())
        self.assertTrue(self.window.searchTheoriesButton.isEnabled())
        self.assertTrue(self.window.actionSettings.isEnabled())

    def test_missing_database_does_not_launch(self):
        self.settings['mysql/database'] = ''
        with patch('gui.search_tab.QtCore.QProcess') as process:
            self.window.searchTheoriesButton.click()
        process.assert_not_called()
        self.assertIn('Cannot search', self.window.searchResultCountEdit.text())
        self.assertFalse(self.window.downloadTheoriesButton.isEnabled())

    def prepare_download(self):
        self.search([{'theory_id': 1}, {'theory_id': 7}, {'theory_id': 99}, {'complete': 3}])
        for field in CSV_FIELDS:
            getattr(self.window, field.widget).setChecked(field.name in ('theory_id', 'input_json'))
        self.destination = self.root / 'saved theories.csv'

    def choose_destination(self, accepted=True):
        execute = patch.object(QtWidgets.QFileDialog, 'exec', return_value=(
            QtWidgets.QDialog.DialogCode.Accepted if accepted else QtWidgets.QDialog.DialogCode.Rejected))
        selected = patch.object(QtWidgets.QFileDialog, 'selectedFiles', return_value=[str(self.destination)])
        return execute, selected

    def download_fixture(self, records=(), *, exit_code=0, stop=False):
        script = f'''
import json, sys, time
request = json.loads(sys.stdin.readline())
assert request['theory_ids'] == [1, 7, 99]
assert request['fields'] == ['theory_id', 'input_json']
assert request['destination'] == {str(self.destination)!r}
assert set(request['settings']) <= {{'tools/processes'}} | {{key for key in request['settings'] if key.startswith('mysql/')}}
if {stop!r}:
    assert sys.stdin.readline().strip() == 'stop'
    print(json.dumps({{'download_cancelled': True}}), flush=True)
else:
    for record in {records!r}:
        print(json.dumps(record), flush=True)
        time.sleep(0.04)
sys.exit({exit_code!r})
'''
        launches = self.launches

        class FixtureProcess(QtCore.QProcess):
            def start(self, program, arguments):
                launches.append((program, arguments))
                super().start(sys.executable, ['-B', '-u', '-c', script])

        return patch('gui.search_tab.QtCore.QProcess', FixtureProcess)

    def completed_download(self, **overrides):
        return {'download_complete': {'written': 3, 'total': 3, 'rows': 5,
                                      'path': str(self.destination), **overrides}}

    def test_download_dialog_selected_fields_and_theory_progress(self):
        self.prepare_download()
        records = [{'download_progress': {'written': 1, 'total': 3}},
                   {'download_progress': {'written': 2, 'total': 3}}, self.completed_download()]
        changes = []
        self.window.searchDownloadProgressBar.valueChanged.connect(changes.append)
        execute, selected = self.choose_destination()
        with execute as dialog, selected, self.download_fixture(records):
            self.window.downloadTheoriesButton.click()
            dialog.assert_called_once()
            self.assertEqual(self.window.downloadTheoriesButton.text(), 'Cancel')
            for control in (self.window.searchTheoriesButton, self.window.searchGaugeGroupsEdit,
                            self.window.searchCsvFieldsGroup, self.window.buildTheoriesButton,
                            self.window.searchEmptyIndicesButton, self.window.actionSettings):
                self.assertFalse(control.isEnabled())
            ticks = []
            QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
            self.wait_search()
            self.assertTrue(ticks)
        self.assertEqual(changes, [33, 66, 100])
        self.assertEqual(self.controller.theory_ids, (1, 7, 99))
        self.assertEqual(self.window.searchResultCountEdit.text(), 'Saved 3 theories (5 CSV rows)')
        self.assertTrue(self.window.downloadTheoriesButton.isEnabled())
        self.assertEqual(self.window.downloadTheoriesButton.text(), 'Download')
        self.assertTrue(self.window.searchCsvFieldsGroup.isEnabled())
        self.assertTrue(self.window.actionSettings.isEnabled())
        self.assertEqual(self.launches[-1][1], ['-python', '-B', '-u', '-m', 'gui.theory_download'])
        self.assertIsNone(self.controller.logger._stream)

    def test_cancel_save_dialog_retains_results_and_does_not_start(self):
        self.prepare_download()
        execute, selected = self.choose_destination(accepted=False)
        with execute, selected, patch('gui.search_tab.QtCore.QProcess') as process:
            self.window.downloadTheoriesButton.click()
        process.assert_not_called()
        self.assertEqual(self.controller.theory_ids, (1, 7, 99))
        self.assertEqual(self.window.searchResultCountEdit.text(), '3 theories found')

    def test_empty_csv_selection_disables_download_without_invalidating_search(self):
        self.prepare_download()
        for field in CSV_FIELDS:
            getattr(self.window, field.widget).setChecked(False)
        self.assertFalse(self.window.downloadTheoriesButton.isEnabled())
        self.assertEqual(self.controller.theory_ids, (1, 7, 99))
        self.window.searchCsvTheoryIdCheckBox.setChecked(True)
        self.assertTrue(self.window.downloadTheoriesButton.isEnabled())

    def test_download_rechecks_database_identity_before_save_dialog(self):
        self.prepare_download()
        self.settings['mysql/database'] = 'another database'
        with patch.object(QtWidgets.QFileDialog, 'exec') as dialog:
            self.window.downloadTheoriesButton.click()
        dialog.assert_not_called()
        self.assertEqual(self.controller.theory_ids, ())
        self.assertIn('Search again', self.window.searchResultCountEdit.toolTip())

    def test_download_failure_retains_ids_and_never_reports_completion(self):
        self.prepare_download()
        cases = [([], 0), ([self.completed_download()], 1),
                 ([{'download_progress': {'written': 3, 'total': 3}}], 0),
                 ([{'download_progress': {'written': 1, 'total': 5}}, self.completed_download()], 0),
                 ([{'download_progress': {'written': True, 'total': 3}}, self.completed_download()], 0),
                 ([self.completed_download(rows=2)], 0), ([self.completed_download(path='wrong.csv')], 0),
                 ([{'download_progress': {'written': 2, 'total': 3}},
                   {'download_progress': {'written': 1, 'total': 3}}, self.completed_download()], 0),
                 ([{'log': 'private dummy: denied', 'level': 'ERROR'}], 1)]
        for records, exit_code in cases:
            execute, selected = self.choose_destination()
            with self.subTest(records=records), execute, selected, self.download_fixture(records, exit_code=exit_code):
                self.window.downloadTheoriesButton.click()
                self.wait_search()
                self.assertEqual(self.controller.theory_ids, (1, 7, 99))
                self.assertIn('failed', self.window.searchResultCountEdit.text())
                self.assertLess(self.window.searchDownloadProgressBar.value(), 100)
                self.assertTrue(self.window.downloadTheoriesButton.isEnabled())
                self.assertNotIn('private dummy', self.window.searchResultCountEdit.toolTip())

    def test_download_cancel_and_window_close_wait_for_cooperative_cleanup(self):
        self.prepare_download()
        for close in (False, True):
            execute, selected = self.choose_destination()
            with self.subTest(close=close), execute, selected, self.download_fixture(stop=True):
                self.window.downloadTheoriesButton.click()
                # Cancel even before the process starts: the stop command must
                # follow the request, and the worker must receive it on stdin.
                if close:
                    self.window.close()
                else:
                    self.window.downloadTheoriesButton.click()
                self.assertIsNotNone(self.controller.process)
                self.assertFalse(self.window.downloadTheoriesButton.isEnabled())
                self.wait_search()
                self.assertEqual(self.controller.theory_ids, (1, 7, 99))
                self.assertIn('Download cancelled', self.window.searchResultCountEdit.text())
                self.assertLess(self.window.searchDownloadProgressBar.value(), 100)


if __name__ == '__main__':
    unittest.main()
