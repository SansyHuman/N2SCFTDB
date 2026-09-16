"""Index worker scheduling, partial progress and optional spawned MySQL checks."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from fractions import Fraction
from multiprocessing import get_context
from pathlib import Path
from queue import Queue
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace
import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from common import n2_theory_db as db
from common import n2_theory_db_indices as backend
from common import n2_theory_properties as properties
from gui import index_calculator as calculator
from test.test_n2_theory_db_indices import SU2, index_payload


def job(number=1):
    return {"theory_id": number, "lagrangian_realization_id": number, "input": SU2,
            "needed_fields": ["superconformal_index", "coulomb_branch_index", "coulomb_branch_spectrum"],
            "unknown_precision": []}


def settings(cache="/unused"):
    return {
        "mysql/database": "isolated_test", "mysql/host": "127.0.0.1", "mysql/port": 3306,
        "mysql/user": "root", "mysql/password": "", "mysql/connect_timeout": 10,
        "index/full_max_order": 4, "index/coulomb_max_dimension": "9/2",
        "tools/processes": 3, "tools/timeout": 30.0,
        "tools/form_executable": "form", "tools/lie_executable": "lie",
        "cache/character_database": str(Path(cache) / "custom-characters.sqlite"),
        "cache/form_database": str(Path(cache) / "other" / "custom-form.sqlite"),
    }


def outcome(item, remaining=()):
    return dict(item, remaining_fields=list(remaining), errors={}, updated_fields=[], skipped_fields={})


def _exit_worker(item):
    os._exit(17)


class IndexJobTests(unittest.TestCase):
    def test_connection_monitor_records_successfully_retried_lock_failures(self):
        from test.test_gui_candidate_workers import _record_connections

        for code in (1205, 1213):
            with self.subTest(code=code):
                connection = MagicMock()
                connection.thread_id.return_value = 123
                original_rollback = connection.rollback
                connection_ids, rollbacks = [], []
                action = MagicMock(side_effect=[db.pymysql.OperationalError(code, "lock failure"), "saved"])
                with patch.object(db, "connect_database", return_value=connection), \
                     _record_connections(connection_ids, rollbacks), patch.object(db.time, "sleep"):
                    tracked = db.connect_database("isolated_test")
                    self.assertEqual(db._run_index_transaction(tracked, action), "saved")
                self.assertEqual(connection_ids, [123])
                self.assertEqual(rollbacks, [123])
                original_rollback.assert_called_once()
                connection.commit.assert_called_once()

    def test_partial_failure_and_stale_job_retry_only_compute_missing_fields(self):
        item = job()
        connection = MagicMock()
        with patch.object(db, "missing_lagrangian_index_fields", return_value=item["needed_fields"][:]), \
             patch.object(properties, "calculate_superconformal_index", side_effect=RuntimeError("FORM failed")), \
             patch.object(properties, "calculate_coulomb_branch_index", return_value="1"), \
             patch.object(properties, "calculate_coulomb_branch_spectrum", return_value=(Fraction(2),)), \
             patch.object(db, "update_lagrangian_indices", side_effect=lambda c, r, p, **kw: {
                 "updated_fields": list(p), "skipped_fields": {},
             }) as save:
            result = backend.calculate_index_job(connection, item, order=4, max_dimension=5, missing_only=True)
        self.assertEqual(result["remaining_fields"], ["superconformal_index"])
        self.assertEqual(result["errors"], {"superconformal_index": "FORM failed"})
        self.assertEqual(save.call_count, 2)
        with patch.object(db, "missing_lagrangian_index_fields", return_value=[]), \
             patch.object(properties, "calculate_superconformal_index") as full, \
             patch.object(db, "update_lagrangian_indices") as save:
            result = backend.calculate_index_job(connection, item, order=4, max_dimension=5, missing_only=True)
        self.assertEqual(result["remaining_fields"], [])
        full.assert_not_called()
        save.assert_not_called()

    def test_stop_saves_active_component_before_returning_remaining_fields(self):
        stopped = Event()
        def full(*args, **kwargs):
            stopped.set()
            return "1"
        with patch.object(properties, "calculate_superconformal_index", side_effect=full), \
             patch.object(properties, "calculate_coulomb_branch_index") as coulomb, \
             patch.object(db, "update_lagrangian_indices", return_value={
                 "updated_fields": ["superconformal_index"], "skipped_fields": {},
             }) as save:
            result = backend.calculate_index_job(MagicMock(), job(), order=4, max_dimension=5,
                                                 cancelled=stopped.is_set)
        save.assert_called_once()
        coulomb.assert_not_called()
        self.assertEqual(result["remaining_fields"], ["coulomb_branch_index", "coulomb_branch_spectrum"])

    def test_full_index_options_override_cache_filenames_and_disable_nested_workers(self):
        options = dict(settings(), **{"tools/form_threads": 4, "tools/tform_executable": "/custom/tform"})
        with patch.multiple(properties, FORM_EXECUTABLE=properties.FORM_EXECUTABLE,
                            TFORM_EXECUTABLE=properties.TFORM_EXECUTABLE, FORM_THREADS=properties.FORM_THREADS,
                            DEFAULT_TIMEOUT=properties.DEFAULT_TIMEOUT), \
             patch.object(calculator, "Finalize"), \
             patch.object(db, "connect_database", return_value=MagicMock()) as connect, \
             patch.object(backend, "calculate_index_job", return_value=outcome(job())) as calculate:
            calculator._initialize_worker(options, Event(), Queue())
            try:
                calculator._calculate_job(job())
                calculator._calculate_job(job(2))
                self.assertEqual(properties.FORM_THREADS, 4)
                self.assertEqual(properties.TFORM_EXECUTABLE, "/custom/tform")
            finally:
                calculator._close_connection()
        connect.assert_called_once()
        self.assertFalse(connect.call_args.kwargs["initialize_schema"])
        forwarded = calculate.call_args.kwargs["full_index_options"]
        self.assertEqual(forwarded["processes"], 1)
        self.assertEqual(forwarded["form_threads"], 4)
        self.assertEqual(forwarded["tform_executable"], "/custom/tform")
        self.assertEqual(forwarded["char_cache_database_path"], options["cache/character_database"])
        self.assertEqual(forwarded["form_cache_database_path"], options["cache/form_database"])


class IndexSchedulingTests(unittest.TestCase):
    def run_pool(self, task, emit, *, cancelled=lambda: False, count=24):
        self.stopped = Event()
        context = SimpleNamespace(Event=lambda: self.stopped)
        context.Manager = lambda: MagicMock(__enter__=lambda _: SimpleNamespace(Queue=Queue),
                                          __exit__=lambda *args: None)
        with patch.object(calculator, "get_context", return_value=context), \
             patch.object(calculator, "ProcessPoolExecutor", side_effect=lambda **kw: ThreadPoolExecutor(
                 max_workers=kw["max_workers"],
             )), patch.object(calculator, "_calculate_job", side_effect=task):
            return calculator.run_calculation([job(i) for i in range(1, count + 1)], settings(),
                                              emit, lambda *args: None, cancelled)

    def test_dynamic_pool_uses_all_workers_and_only_coordinator_emits(self):
        barrier, lock = Barrier(3), Lock()
        active = maximum = 0
        started, results = [], []
        coordinator = get_ident()
        def task(item):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                started.append(item["theory_id"])
            if item["theory_id"] <= 3:
                barrier.wait(timeout=10)
            time.sleep(0.05 if item["theory_id"] == 1 else 0.001)
            with lock:
                active -= 1
            return outcome(item)
        def emit(record):
            self.assertEqual(get_ident(), coordinator)
            if "result" in record:
                results.append(record["result"]["theory_id"])
        summary = self.run_pool(task, emit)
        self.assertEqual(summary["status"], "completed")
        self.assertCountEqual(started, range(1, 25))
        self.assertCountEqual(results, started)
        self.assertEqual(maximum, 3)
        self.assertNotEqual(results[0], 1)

    def test_stop_bounds_queue_and_drains_results(self):
        barrier, stop = Barrier(3), Event()
        started, results = [], []
        def task(item):
            started.append(item["theory_id"])
            if self.stopped.is_set():
                return outcome(item, item["needed_fields"])
            if item["theory_id"] <= 3:
                barrier.wait(timeout=10)
                stop.set()
                time.sleep(0.05)
            return outcome(item)
        summary = self.run_pool(task, results.append, cancelled=stop.is_set, count=100)
        self.assertEqual(summary["status"], "stopped")
        self.assertLessEqual(len(started), 6)
        self.assertEqual(len([r for r in results if "result" in r]), len(started))

    def test_invalid_settings_or_duplicate_jobs_never_spawn(self):
        for jobs, opts in (([job(), job()], settings()), ([job()], dict(settings(), **{"tools/processes": 0})),
                           ([job()], dict(settings(), **{"index/coulomb_max_dimension": "-1/3"}))):
            with patch.object(calculator, "get_context") as spawn:
                summary = calculator.run_calculation(jobs, opts, lambda r: None, lambda *args: None)
                self.assertEqual(summary["status"], "failed")
                spawn.assert_not_called()

    def test_index_workers_share_the_cpu_budget_with_form_threads(self):
        jobs = [job(i) for i in range(1, 65)]
        for cpu_count, threads, expected in ((32, 1, 32), (32, 8, 4), (32, 32, 1),
                                             (10, 4, 2), (2, 8, 1), (-1, 8, 4)):
            options = dict(settings(), **{"tools/processes": cpu_count, "tools/form_threads": threads})
            with self.subTest(cpu_count=cpu_count, threads=threads), \
                 patch.object(calculator.os, "cpu_count", return_value=32):
                normalized, workers = calculator._validate_request(jobs, options)
                self.assertEqual(workers, expected)
                self.assertEqual(normalized["tools/form_threads"], threads)
                self.assertEqual(calculator._validate_request(jobs[:1], options)[1], 1)
        with patch.object(calculator.os, "cpu_count", return_value=None):
            self.assertEqual(calculator._validate_request(jobs, dict(settings(), **{"tools/processes": -1}))[1], 1)

    def test_invalid_form_threads_never_spawn(self):
        for threads in (0, -1, True, 2.0):
            with self.subTest(threads=threads), patch.object(calculator, "get_context") as spawn:
                options = dict(settings(), **{"tools/form_threads": threads})
                summary = calculator.run_calculation([job()], options, lambda r: None, lambda *args: None)
                self.assertEqual(summary["status"], "failed")
                spawn.assert_not_called()

    def test_abrupt_spawned_worker_exit_does_not_hang_or_replay(self):
        records = []
        with patch.object(calculator, "_calculate_job", _exit_worker):
            summary = calculator.run_calculation([job(i) for i in range(1, 20)], settings(),
                                                  records.append, lambda *args: None)
        self.assertEqual(summary["status"], "failed")
        self.assertLessEqual(summary["processed"], 6)
        self.assertTrue(any(r.get("result", {}).get("errors") for r in records))


def _tracked_initializer(options, stopped, messages, connection_ids, rollbacks):
    from test.test_gui_candidate_workers import _record_connections
    _record_connections(connection_ids, rollbacks).start()
    calculator._initialize_worker(options, stopped, messages)


MYSQL_DATABASE = os.environ.get("N2_TEST_MYSQL_DATABASE")


@unittest.skipUnless(MYSQL_DATABASE, "set N2_TEST_MYSQL_DATABASE for spawned index tests")
class IndexCalculationMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "test" not in MYSQL_DATABASE.lower():
            raise RuntimeError("Use a dedicated test database")
        cls.options = {"host": os.environ.get("N2_TEST_MYSQL_HOST", "127.0.0.1"),
                       "port": int(os.environ.get("N2_TEST_MYSQL_PORT", "3306")),
                       "user": os.environ.get("N2_TEST_MYSQL_USER", "root"),
                       "password": os.environ.get("N2_TEST_MYSQL_PASSWORD", ""),
                       "unix_socket": os.environ.get("N2_TEST_MYSQL_UNIX_SOCKET")}
        cls.connection = db.connect_database(MYSQL_DATABASE, **cls.options)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        db._execute(self.connection, "DELETE FROM theories")
        temp = tempfile.TemporaryDirectory(prefix="n2-index-workers-")
        self.addCleanup(temp.cleanup)
        self.settings = settings(temp.name)
        self.settings["mysql/database"] = MYSQL_DATABASE
        self.settings.update({f"mysql/{k}": v for k, v in self.options.items()})
        self.records = []
        manager = get_context("spawn").Manager()
        self.addCleanup(manager.shutdown)
        self.connection_ids = manager.list()
        self.rollbacks = manager.list()
        def executor(**kwargs):
            kwargs["initializer"] = _tracked_initializer
            kwargs["initargs"] = (*kwargs["initargs"], self.connection_ids, self.rollbacks)
            return ProcessPoolExecutor(**kwargs)
        factory = patch.object(calculator, "ProcessPoolExecutor", side_effect=executor)
        factory.start()
        self.addCleanup(factory.stop)

    def add_theories(self, count):
        for n in range(count):
            data = {"algebra": "A1", "hypermultiplets": [
                {"dynkin_labels": [1], "number": 4, "kind": "full"},
                {"dynkin_labels": [0], "number": n, "kind": "full"},
            ]}
            db.store_lagrangian_theory(self.connection, data, initialize_schema=False)
        return list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0))

    def run_jobs(self, jobs, cancelled=lambda: False):
        result = calculator.run_calculation(jobs, self.settings, self.records.append, lambda *args: None, cancelled)
        placeholders = ", ".join("%s" for _ in self.connection_ids)
        self.assertTrue(placeholders)
        row = db._fetchone(self.connection, f"SELECT COUNT(*) AS n FROM information_schema.PROCESSLIST "
                          f"WHERE ID IN ({placeholders})", tuple(self.connection_ids))
        self.assertEqual(row["n"], 0, "worker MySQL connection leaked after shutdown")
        return result

    def test_actual_indices_exact_cutoffs_custom_caches_and_stale_retry(self):
        jobs = self.add_theories(4)
        product = {
            "gauge_groups": [{"id": "left", "algebra": "A1"}, {"id": "right", "algebra": "A1"}],
            "hypermultiplets": [
                {"representations": {"left": "fundamental", "right": "singlet"}, "number": 4},
                {"representations": {"left": "singlet", "right": "fundamental"}, "number": 4},
            ],
        }
        db.store_lagrangian_theory(self.connection, product, initialize_schema=False)
        product_job = list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0))[-1]
        selected = jobs[:2] + [product_job]
        summary = self.run_jobs(selected)
        self.assertEqual(summary["status"], "completed", self.records)
        pids = {r["result"]["worker_pid"] for r in self.records if "result" in r}
        self.assertGreater(len(pids), 1)
        self.assertNotIn(os.getpid(), pids)
        self.assertTrue(Path(self.settings["cache/character_database"]).is_file())
        self.assertTrue(Path(self.settings["cache/form_database"]).is_file())
        first = db._fetchone(self.connection, "SELECT * FROM theory_properties WHERE theory_id = %s",
                             (jobs[0]["theory_id"],))
        self.assertEqual(json.loads(first["coulomb_branch_index_json"]), "1 + x^2 + x^4")
        self.assertEqual(json.loads(first["coulomb_branch_index_max_dimension_json"]), {"numerator": 9, "denominator": 2})
        self.assertEqual(first["superconformal_index_order"], 4)
        from index.n2_theory_index import parse_index_polynomial
        self.assertEqual(parse_index_polynomial(json.loads(first["superconformal_index_json"])),
                         parse_index_polynomial("1 + 28*t^4/u^2 + t^4*u^4"))
        remaining = list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0))
        self.assertEqual(remaining, jobs[2:])
        self.records.clear()
        self.settings["tools/form_executable"] = "/missing/form"
        self.settings["index/full_max_order"] = 100  # A stale GUI selection must not upgrade.
        self.assertEqual(self.run_jobs(selected)["status"], "completed")
        self.assertTrue(all(not r["result"]["updated_fields"] for r in self.records if "result" in r))
        self.assertEqual(list(self.rollbacks), [], "index workers unexpectedly rolled back/retried")

    def test_partial_tool_failure_commits_spectrum_and_retries_only_indices(self):
        jobs = self.add_theories(2)
        self.settings["tools/form_executable"] = "/missing/form"
        summary = self.run_jobs(jobs)
        self.assertEqual(summary["failed"], 2)
        results = [r["result"] for r in self.records if "result" in r]
        self.assertTrue(all(r["remaining_fields"] == ["superconformal_index", "coulomb_branch_index"] for r in results))
        self.assertTrue(all("coulomb_branch_spectrum" in r["updated_fields"] for r in results))
        self.settings["tools/form_executable"] = "form"
        self.records.clear()
        self.assertEqual(self.run_jobs(jobs)["status"], "completed", self.records)
        self.assertTrue(all("coulomb_branch_spectrum" not in r["result"]["updated_fields"]
                            for r in self.records if "result" in r))

    def test_256_distinct_theories_eight_workers_no_deadlocks(self):
        jobs = self.add_theories(256)
        self.settings.update({"tools/processes": 8, "index/full_max_order": 0, "index/coulomb_max_dimension": "0"})
        summary = self.run_jobs(jobs)
        self.assertEqual(summary["status"], "completed", self.records)
        self.assertEqual(summary["processed"], 256)
        self.assertEqual(list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0)), [])
        self.assertEqual(list(self.rollbacks), [], "index workers unexpectedly rolled back/retried")

    def test_real_gui_search_calculation_and_file_log(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtCore, QtTest, QtWidgets
        from gui.n2_db import N2DatabaseWindow, SettingsStore, default_settings
        from test.test_gui_n2_db import MemoryVault
        jobs = self.add_theories(2)
        type(self).app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        root = Path(self.settings["cache/character_database"]).parent
        store = SettingsStore(root / "gui.ini", vault=MemoryVault())
        store.save(dict(default_settings(), **self.settings))
        with patch("gui.n2_db.PROJECT_ROOT", root):
            window = N2DatabaseWindow(store)
        self.addCleanup(window.close)
        class LocalProcess(QtCore.QProcess):
            def setWorkingDirectory(self, directory):
                super().setWorkingDirectory(str(Path(__file__).resolve().parents[1]))
        def wait_process():
            deadline = time.monotonic() + 45
            while window.index_tab.process is not None and time.monotonic() < deadline:
                QtTest.QTest.qWait(20)
            if window.index_tab.process is not None:
                window.index_tab.cancel_for_close()
                window.index_tab.process.waitForFinished(10000)
            self.assertIsNone(window.index_tab.process, window.indexCalculationLog.toPlainText())
        with patch("gui.index_tab.QtCore.QProcess", LocalProcess), \
             patch.dict(os.environ, {"N2_DB_UNIX_SOCKET": self.options["unix_socket"] or ""}):
            window.searchEmptyIndicesButton.click()
            wait_process()
            self.assertEqual(len(window.index_tab.retrieved_jobs), 2, window.indexCalculationLog.toPlainText())
            window.selectAllIndexGroupsCheckBox.click()
            self.assertTrue(window.calculateIndexButton.isEnabled())
            window.calculateIndexButton.click()
            wait_process()
        self.assertEqual(window.index_tab.retrieved_jobs, (), window.indexCalculationLog.toPlainText())
        self.assertEqual(list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0)), [])
        self.assertIsNone(window.index_tab.logger._stream)
        logs = list((root / "logs").glob("log_index_*.log"))
        self.assertEqual(len(logs), 2)
        text = window.index_tab.logger.path.read_text()
        self.assertIn("Index calculation completed: 2/2", text)
        self.assertIn("superconformal_index saved", text)
        self.assertNotIn(self.settings["mysql/password"] or "unlikely-secret", text)

    def test_stop_in_spawned_pool_retains_unfinished_work_and_closes_connections(self):
        jobs = self.add_theories(32)
        self.settings.update({"index/full_max_order": 0, "index/coulomb_max_dimension": "0"})
        stop = Event()
        class StopRecords(list):
            def append(self, record):
                super().append(record)
                if "result" in record and not record["result"]["remaining_fields"]:
                    stop.set()
        self.records = StopRecords()
        summary = self.run_jobs(jobs, stop.is_set)
        self.assertEqual(summary["status"], "stopped")
        self.assertLessEqual(summary["processed"], 6)
        completed = sum(not r["result"]["remaining_fields"] for r in self.records if "result" in r)
        self.assertGreater(completed, 0)
        remaining = list(db.iter_lagrangian_index_jobs(self.connection, order=0, max_dimension=0))
        self.assertEqual(len(remaining), 32 - completed)


if __name__ == "__main__":
    unittest.main()
