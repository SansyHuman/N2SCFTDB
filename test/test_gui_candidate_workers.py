"""Bounded scheduling checks and optional real spawned-worker/MySQL tests."""

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict
from multiprocessing import get_context
import os
from threading import Barrier, Event, Lock, get_ident
import time
import unittest
from unittest.mock import MagicMock, patch

from gui import candidate_workers as workers
from gui import theory_builder
from common import n2_theory_db as database
from common import n2_theory_iter as theories
from index import char_decomposition_cache as cache


def _exit_worker(candidate, context, collect):
    os._exit(17)


class SchedulingTests(unittest.TestCase):
    def run_pool(self, candidates, task, report, cancelled=lambda: False):
        def executor(**kwargs):
            self.assertEqual(kwargs["mp_context"].get_start_method(), "spawn")
            self.assertIs(kwargs["initializer"], workers._initialize_worker)
            self.stop_event = kwargs["initargs"][-1]
            return ThreadPoolExecutor(max_workers=kwargs["max_workers"])

        # Synthetic tasks do no Sage/SQL work in threads. This makes queue and
        # Stop timing deterministic; the integration tests use real processes.
        with patch.object(workers, "ProcessPoolExecutor", side_effect=executor), \
             patch.object(workers, "_candidate_task", side_effect=task):
            workers.process_candidates(candidates, "A1", 3, "test_db", {},
                                       None, False, report, lambda *args: None, cancelled)

    def test_dynamic_distribution_and_only_coordinator_reports(self):
        barrier, lock = Barrier(3), Lock()
        active = maximum = 0
        started, received = [], []
        coordinator = get_ident()

        def task(candidate, context, collect):
            nonlocal active, maximum
            with lock:
                started.append(candidate)
                active += 1
                maximum = max(maximum, active)
            if candidate < 3:
                barrier.wait(timeout=10)
            time.sleep(0.06 if candidate == 0 else 0.001)
            with lock:
                active -= 1
            result = workers.CandidateResult()
            result.counts.valid = 1
            result.messages = [(str(candidate), "INFO")]
            return result

        def report(result):
            self.assertEqual(get_ident(), coordinator)
            received.append(int(result.messages[0][0]))

        self.run_pool(list(range(24)), task, report)
        self.assertCountEqual(started, range(24))
        self.assertCountEqual(received, range(24))
        self.assertEqual(maximum, 3)
        self.assertNotEqual(received[0], 0)

    def test_stop_bounds_submission_and_drains_committed_results(self):
        barrier, stop = Barrier(3), Event()
        received, started = [], []

        def task(candidate, context, collect):
            started.append(candidate)
            result = workers.CandidateResult()
            if self.stop_event.is_set():
                return result
            if candidate < 3:
                barrier.wait(timeout=10)
                if candidate == 0:
                    stop.set()
                else:
                    time.sleep(0.05)
                result.counts.valid = result.counts.added = 1
            return result

        with self.assertRaises(workers.BuildCancelled):
            self.run_pool(list(range(100)), task, received.append, stop.is_set)
        self.assertLessEqual(len(started), 6)
        self.assertEqual(sum(result.counts.added for result in received), 3)
        self.assertTrue(self.stop_event.is_set())

    def test_broken_pool_is_reported_without_replaying_unknown_writes(self):
        started, received = [], []

        def task(candidate, context, collect):
            started.append(candidate)
            raise BrokenProcessPool("worker exited unexpectedly")

        with self.assertRaisesRegex(RuntimeError, "remaining candidates"):
            self.run_pool(list(range(100)), task, received.append)
        self.assertLessEqual(len(started), 6)
        self.assertEqual(len(started), len(set(started)))
        self.assertTrue(any("database outcome may be unknown" in message
                            for result in received for message, level in result.messages))

    def test_worker_reuses_own_connection_and_reopens_after_insert_failure(self):
        first, second = MagicMock(), MagicMock()
        options = {"host": "db.invalid", "user": "test"}
        with patch.object(workers, "Finalize"), \
             patch.object(database, "connect_database", side_effect=[first, second]) as connect, \
             patch.object(database, "store_lagrangian_theory", side_effect=[
                 RuntimeError("connection lost"), MagicMock(inserted=True, theory_id=1),
             ]):
            workers._initialize_worker("test_db", options, Event())
            self.addCleanup(workers._close_worker_connection)
            self.assertIs(workers._get_worker_connection(), first)
            self.assertIs(workers._get_worker_connection(), first)
            candidate = theories.enumerate_simple_theory_candidates("A1")[0]
            failed = workers._candidate_task(candidate, "A1", True)
            success = workers._candidate_task(candidate, "A1", True)
            self.assertEqual(failed.counts.db_failed, 1)
            self.assertEqual(success.counts.added, 1)
            self.assertEqual(failed.representations, success.representations)
            self.assertEqual(connect.call_count, 2)
            connect.assert_called_with("test_db", **options, initialize_schema=False)
            first.close.assert_called_once()
            workers._close_worker_connection()
            second.close.assert_called_once()

    def test_spawned_worker_death_is_drained_without_hanging(self):
        results = []
        with patch.object(workers, "_candidate_task", _exit_worker):
            with self.assertRaisesRegex(RuntimeError, "remaining candidates"):
                workers.process_candidates(list(range(100)), "A1", 2, "unused_test", {},
                                           None, False, results.append, lambda *args: None, lambda: False)
        self.assertTrue(results)
        self.assertTrue(all(result.counts.check_failed == 1 for result in results))
        self.assertLessEqual(len(results), 4)

    def test_pool_failure_during_submission_preserves_completed_result(self):
        committed = workers.CandidateResult()
        committed.counts.valid = committed.counts.added = 1
        future = Future()
        future.set_result(committed)
        executor = MagicMock()
        executor.__enter__.return_value = executor
        executor.submit.side_effect = [future, BrokenProcessPool("failed during submission")]
        results = []
        with patch.object(workers, "ProcessPoolExecutor", return_value=executor):
            with self.assertRaisesRegex(RuntimeError, "failed during submission"):
                workers.process_candidates(list(range(10)), "A1", 2, "unused_test", {},
                                           None, False, results.append, lambda *args: None, lambda: False)
        self.assertEqual(results, [committed])


def _race_import(database_name, options, candidate, barrier):
    """Force two independent MySQL sessions past the same absent-row lookup."""
    connection = database.connect_database(database_name, **options, initialize_schema=False)
    original = database._find_stored_realization
    first_lookup = True

    def find(*args):
        nonlocal first_lookup
        found = original(*args)
        if first_lookup:
            first_lookup = False
            if found is not None:
                raise AssertionError("race requires an empty database")
            barrier.wait(timeout=30)
        return found

    try:
        with patch.object(database, "_find_stored_realization", side_effect=find):
            stored = database.store_lagrangian_theory(connection, candidate, initialize_schema=False)
        return asdict(stored), os.getpid(), connection.thread_id()
    finally:
        connection.close()


def _record_connections(connection_ids):
    """Instrument only this process's database API; keep other clients out."""
    original = database.connect_database

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        try:
            connection_ids.append(connection.thread_id())
        except BaseException:
            connection.close()
            raise
        return connection

    return patch.object(database, "connect_database", side_effect=connect)


def _initialize_tracked_worker(database_name, options, stopped, connection_ids):
    # Spawn does not inherit the coordinator's mocks. Record real connection
    # IDs in the child while retaining the production initializer/finalizer.
    _record_connections(connection_ids).start()
    workers._initialize_worker(database_name, options, stopped)


MYSQL_TEST_DATABASE = os.environ.get("N2_TEST_MYSQL_DATABASE")


@unittest.skipUnless(MYSQL_TEST_DATABASE, "set N2_TEST_MYSQL_DATABASE for spawned MySQL tests")
class ParallelMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "test" not in MYSQL_TEST_DATABASE.lower():
            raise RuntimeError("Use a dedicated test database")
        cls.options = {
            "host": os.environ.get("N2_TEST_MYSQL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("N2_TEST_MYSQL_PORT", "3306")),
            "user": os.environ.get("N2_TEST_MYSQL_USER", "root"),
            "password": os.environ.get("N2_TEST_MYSQL_PASSWORD", ""),
            "unix_socket": os.environ.get("N2_TEST_MYSQL_UNIX_SOCKET"),
            "connect_timeout": 10,
        }
        cls.connection = database.connect_database(MYSQL_TEST_DATABASE, **cls.options)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        database._execute(self.connection, "DELETE FROM theories")
        # Simulate an unrelated database browser left open during every test.
        self.other_client = database.pymysql.connect(database=MYSQL_TEST_DATABASE, **self.options)
        self.addCleanup(self.other_client.close)
        manager = get_context("spawn").Manager()
        self.addCleanup(manager.shutdown)
        self.connection_ids = manager.list()
        recording = _record_connections(self.connection_ids)
        recording.start()
        self.addCleanup(recording.stop)

        def executor(**kwargs):
            self.assertIs(kwargs["initializer"], workers._initialize_worker)
            kwargs["initializer"] = _initialize_tracked_worker
            kwargs["initargs"] = (*kwargs["initargs"], self.connection_ids)
            return ProcessPoolExecutor(**kwargs)

        factory = patch.object(workers, "ProcessPoolExecutor", side_effect=executor)
        factory.start()
        self.addCleanup(factory.stop)

    def count(self, table):
        return database._fetchone(self.connection, f"SELECT COUNT(*) AS n FROM {table}")["n"]

    def assert_workers_disconnected(self, timeout=5):
        owned = set(self.connection_ids)
        self.assertTrue(owned, "No test connections were recorded; cleanup was not checked")
        placeholders = ", ".join(["%s"] * len(owned))
        deadline = time.monotonic() + timeout
        while True:
            with self.connection.cursor() as cursor:
                cursor.execute(f"SELECT ID FROM information_schema.PROCESSLIST WHERE ID IN ({placeholders})",
                               tuple(sorted(owned)))
                remaining = {row["ID"] for row in cursor.fetchall()}
            if not remaining:
                return
            if time.monotonic() >= deadline:
                self.fail(f"Test-owned MySQL connections still open: {sorted(remaining)}")
            # Allow MySQL to process socket closure after a worker has exited.
            time.sleep(0.05)

    def test_cleanup_check_ignores_other_clients_but_detects_owned_connection(self):
        # A client connecting after the test starts must also be ignored.
        other = database.pymysql.connect(database=MYSQL_TEST_DATABASE, **self.options)
        self.addCleanup(other.close)
        owned = database.connect_database(MYSQL_TEST_DATABASE, **self.options, initialize_schema=False)
        try:
            with self.assertRaisesRegex(AssertionError, "Test-owned MySQL connections still open"):
                self.assert_workers_disconnected(timeout=0)
        finally:
            owned.close()
        self.assert_workers_disconnected()
        self.other_client.ping(reconnect=False)
        other.ping(reconnect=False)

    def test_simultaneous_equivalent_imports_have_one_winner_without_orphans(self):
        full = {"algebra": "A1", "hypermultiplets": [{"dynkin_labels": [1], "number": 4, "kind": "full"}]}
        half = {"algebra": "A1", "hypermultiplets": [{"dynkin_labels": [1], "number": 8, "kind": "half"}]}
        spawn = get_context("spawn")
        with spawn.Manager() as manager:
            barrier = manager.Barrier(2)
            with ProcessPoolExecutor(max_workers=2, mp_context=spawn) as executor:
                futures = [executor.submit(_race_import, MYSQL_TEST_DATABASE, self.options, data, barrier)
                           for data in (full, half)]
                results = [future.result(timeout=60) for future in futures]
        self.connection_ids.extend([connection_id for _, _, connection_id in results])
        self.assertEqual(sum(stored["inserted"] for stored, _, _ in results), 1)
        self.assertEqual(len({stored["theory_id"] for stored, _, _ in results}), 1)
        self.assertEqual(len({pid for _, pid, _ in results}), 2)
        self.assertEqual(len({connection_id for _, _, connection_id in results}), 2)
        for table in ("theories", "theory_properties", "lagrangian_realizations", "gauge_factors", "hypermultiplets"):
            self.assertEqual(self.count(table), 1, table)
        self.assert_workers_disconnected()

    def test_parallel_build_matches_serial_counts_and_cache_union_then_reuses_db(self):
        settings = {f"mysql/{key}": value for key, value in self.options.items()}
        settings.update({"mysql/database": MYSQL_TEST_DATABASE, "index/full_max_order": 4,
                         "cache/character_database": "unused-test-cache.db",
                         "tools/lie_executable": "must-not-run", "tools/timeout": 1,
                         "tools/processes": 1})
        coordinator = os.getpid()
        logs, pids = [], set()

        def log(message, level="INFO"):
            self.assertEqual(os.getpid(), coordinator)
            logs.append((message, level))

        with patch.object(cache, "build_decomposition_cache") as build:
            serial = theory_builder.run_build("A1\nA1,A1", settings, True, log)
            serial_reps = {call.args[:2] for call in build.call_args_list}
        database._execute(self.connection, "DELETE FROM theories")
        settings["tools/processes"] = 3

        def observe(*args):
            arguments = list(args)
            original_report = arguments[7]

            def report(result):
                pids.add(result.worker_pid)
                original_report(result)

            arguments[7] = report
            return workers.process_candidates(*arguments)

        with patch.object(theory_builder, "process_candidates", side_effect=observe), \
             patch.object(cache, "build_decomposition_cache") as build:
            parallel = theory_builder.run_build("A1\nA1,A1", settings, True, log)
        self.assertEqual(parallel, serial)
        self.assertEqual(parallel["added"], 10)
        self.assertEqual(parallel["errors"], 0, logs)
        self.assertEqual({call.args[:2] for call in build.call_args_list}, serial_reps)
        self.assertGreaterEqual(len(pids), 2)
        self.assertNotIn(coordinator, pids)
        repeated = theory_builder.run_build("A1\nA1,A1", settings, False, log)
        self.assertEqual((repeated["added"], repeated["existing"], repeated["errors"]), (0, 10, 0), logs)
        self.assertEqual(self.count("theories"), 10)
        self.assert_workers_disconnected()

    def test_parallel_candidates_include_duplicates_invalids_and_representation_union(self):
        valid = theories.enumerate_simple_theory_candidates("A1")[0]
        invalid = {"algebra": "A1", "hypermultiplets": []}
        results = []
        workers.process_candidates([valid] * 16 + [invalid] * 4, "A1", 3,
                                   MYSQL_TEST_DATABASE, self.options, self.connection,
                                   True, results.append, lambda *args: None, lambda: False)
        counts = workers.Counts()
        for result in results:
            counts.include(result.counts)
        self.assertEqual((counts.valid, counts.invalid, counts.added, counts.existing), (16, 4, 1, 15))
        self.assertEqual((counts.check_failed, counts.db_failed), (0, 0))
        self.assertEqual(len(results), 20)
        self.assertTrue(all(result.representations for result in results if result.counts.valid))
        self.assertEqual(self.count("theories"), 1)
        self.assert_workers_disconnected()

    def test_stop_in_real_pool_counts_all_committed_inserts_and_closes_workers(self):
        # Distinct free-hyper multiplicities make every committed candidate
        # observable as its own theory row, even if Stop races with commit.
        candidates = [{"algebra": "A1", "hypermultiplets": [
            {"dynkin_labels": [1], "number": 4, "kind": "full"},
            {"dynkin_labels": [0], "number": n, "kind": "full"},
        ]} for n in range(1, 101)]
        stopped, counts = Event(), workers.Counts()

        def report(result):
            counts.include(result.counts)
            if result.counts.added:
                stopped.set()

        with self.assertRaises(workers.BuildCancelled):
            workers.process_candidates(candidates, "A1", 3, MYSQL_TEST_DATABASE,
                                       self.options, self.connection, False, report,
                                       lambda *args: None, stopped.is_set)
        self.assertGreater(counts.added, 0)
        self.assertLessEqual(counts.valid, 6)
        self.assertEqual((counts.invalid, counts.check_failed, counts.db_failed), (0, 0, 0))
        self.assertEqual(self.count("theories"), counts.added)
        self.assert_workers_disconnected()


if __name__ == "__main__":
    unittest.main()
