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
from gui import group_workers
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


def _record_connections(connection_ids, rollbacks=None):
    """Instrument only this process's database API; keep other clients out."""
    original = database.connect_database

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        try:
            connection_ids.append(connection.thread_id())
            if rollbacks is not None:
                original_rollback = connection.rollback

                def rollback():
                    # Record even a retry that subsequently succeeds. This
                    # needs no server-wide PROCESS/INNODB_METRICS privilege.
                    rollbacks.append(connection.thread_id())
                    return original_rollback()

                connection.rollback = rollback
        except BaseException:
            connection.close()
            raise
        return connection

    return patch.object(database, "connect_database", side_effect=connect)


def _synchronized_new_import(database_name, options, candidate, barrier):
    """Align fresh property inserts to expose missing-row gap-lock cycles."""
    connection = database.connect_database(database_name, **options, initialize_schema=False)
    original = database._execute
    first_insert = True

    def execute(connection, statement, parameters=()):
        nonlocal first_insert
        if first_insert and "INSERT INTO theory_properties(" in statement:
            first_insert = False
            barrier.wait(timeout=30)
        return original(connection, statement, parameters)

    try:
        with patch.object(database, "_execute", side_effect=execute), \
             patch.object(connection, "rollback", wraps=connection.rollback) as rollback:
            stored = database.store_lagrangian_theory(connection, candidate, initialize_schema=False)
        return asdict(stored), rollback.call_count, connection.thread_id()
    finally:
        connection.close()


def _initialize_tracked_worker(database_name, options, stopped, connection_ids):
    # Spawn does not inherit the coordinator's mocks. Record real connection
    # IDs in the child while retaining the production initializer/finalizer.
    _record_connections(connection_ids).start()
    workers._initialize_worker(database_name, options, stopped)


def _initialize_tracked_group_worker(database_name, options, stopped, events, connection_ids):
    _record_connections(connection_ids).start()
    group_workers._initialize_worker(database_name, options, stopped, events)


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
            if kwargs["initializer"] is workers._initialize_worker:
                kwargs["initializer"] = _initialize_tracked_worker
            else:
                self.assertIs(kwargs["initializer"], group_workers._initialize_worker)
                kwargs["initializer"] = _initialize_tracked_group_worker
            kwargs["initargs"] = (*kwargs["initargs"], self.connection_ids)
            return ProcessPoolExecutor(**kwargs)

        factory = patch.object(workers, "ProcessPoolExecutor", side_effect=executor)
        factory.start()
        self.addCleanup(factory.stop)
        group_factory = patch.object(group_workers, "ProcessPoolExecutor", side_effect=executor)
        group_factory.start()
        self.addCleanup(group_factory.stop)

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
        self.assert_concurrent_duplicates(full, half)

    def test_simultaneous_triality_images_have_one_winner_without_orphans(self):
        vector = {"algebra": "D4", "hypermultiplets": [{"representation": "vector", "number": 6}]}
        spinor = {"algebra": "D4", "hypermultiplets": [{"representation": "spinor", "number": 6}]}
        self.assert_concurrent_duplicates(vector, spinor)

    def test_simultaneous_factor_permutations_have_one_winner_without_orphans(self):
        from test.test_factor_permutations import MIXED_PRODUCT, reordered
        self.assert_concurrent_duplicates(
            MIXED_PRODUCT, reordered(MIXED_PRODUCT, (1, 0), rename=True),
            gauge_factors=2, hypermultiplets=3,
        )

    def assert_concurrent_duplicates(self, first, second, *, gauge_factors=1, hypermultiplets=1):
        spawn = get_context("spawn")
        with spawn.Manager() as manager:
            barrier = manager.Barrier(2)
            with ProcessPoolExecutor(max_workers=2, mp_context=spawn) as executor:
                futures = [executor.submit(_race_import, MYSQL_TEST_DATABASE, self.options, data, barrier)
                           for data in (first, second)]
                results = [future.result(timeout=60) for future in futures]
        self.connection_ids.extend([connection_id for _, _, connection_id in results])
        self.assertEqual(sum(stored["inserted"] for stored, _, _ in results), 1)
        self.assertEqual(len({stored["theory_id"] for stored, _, _ in results}), 1)
        self.assertEqual(len({pid for _, pid, _ in results}), 2)
        self.assertEqual(len({connection_id for _, _, connection_id in results}), 2)
        for table in ("theories", "theory_properties", "lagrangian_realizations"):
            self.assertEqual(self.count(table), 1, table)
        self.assertEqual(self.count("gauge_factors"), gauge_factors)
        self.assertEqual(self.count("hypermultiplets"), hypermultiplets)
        self.assert_workers_disconnected()

    def test_distinct_new_theories_do_not_deadlock_on_missing_properties(self):
        candidates = [{"algebra": "A1", "hypermultiplets": [
            {"dynkin_labels": [1], "number": 4, "kind": "full"},
            {"dynkin_labels": [0], "number": n, "kind": "full"},
        ]} for n in (1, 2)]
        spawn = get_context("spawn")
        with spawn.Manager() as manager:
            barrier = manager.Barrier(2)
            with ProcessPoolExecutor(max_workers=2, mp_context=spawn) as executor:
                futures = [executor.submit(_synchronized_new_import, MYSQL_TEST_DATABASE,
                                           self.options, candidate, barrier)
                           for candidate in candidates]
                results = [future.result(timeout=60) for future in futures]
        self.connection_ids.extend([connection_id for _, _, connection_id in results])
        self.assertTrue(all(stored["inserted"] for stored, _, _ in results))
        self.assertEqual(sum(rollbacks for _, rollbacks, _ in results), 0,
                         "Independent fresh inserts should not need deadlock retries")
        self.assertEqual(self.count("theories"), 2)
        self.assertEqual(self.count("theory_properties"), 2)
        self.assert_workers_disconnected()

    def test_parallel_build_matches_serial_counts_and_cache_union_then_reuses_db(self):
        settings = {f"mysql/{key}": value for key, value in self.options.items()}
        settings.update({"mysql/database": MYSQL_TEST_DATABASE, "index/full_max_order": 4,
                         "cache/character_database": "unused-test-cache.db",
                         "tools/lie_executable": "must-not-run", "tools/timeout": 1,
                         "tools/processes": 1})
        coordinator = os.getpid()
        logs, pids, group_pids, phases = [], set(), {}, {}

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
            original_report = arguments[6]

            def report(event):
                pids.add(event.result.worker_pid)
                group_pids.setdefault(event.line_number, set()).add(event.result.worker_pid)
                phases.setdefault(event.line_number, []).append(event.phase)
                original_report(event)

            arguments[6] = report
            return group_workers.process_groups(*arguments)

        with patch.object(theory_builder, "process_groups", side_effect=observe), \
             patch.object(theories, "enumerate_simple_theory_candidates",
                          side_effect=AssertionError("coordinator must not enumerate")), \
             patch.object(theories, "enumerate_product_theory_candidates",
                          side_effect=AssertionError("coordinator must not enumerate")), \
             patch.object(cache, "build_decomposition_cache") as build:
            parallel = theory_builder.run_build("A1\nA1,A1", settings, True, log)
        self.assertEqual(parallel, serial)
        # Two A1 candidates plus four connected A1 x A1 candidates.
        # One pair of product candidates differs only by exchanging factors.
        self.assertEqual((parallel["candidates"], parallel["valid"]), (6, 6))
        self.assertEqual((parallel["added"], parallel["existing"]), (5, 1))
        self.assertEqual(parallel["errors"], 0, logs)
        self.assertEqual({call.args[:2] for call in build.call_args_list}, serial_reps)
        self.assertGreaterEqual(len(pids), 2)
        self.assertNotIn(coordinator, pids)
        self.assertEqual({line: len(owners) for line, owners in group_pids.items()}, {1: 1, 2: 1})
        self.assertEqual(phases[1], ["started", "enumerated", "candidate", "candidate", "finished"])
        self.assertEqual(phases[2], ["started", "enumerated"] + ["candidate"] * 4 + ["finished"])
        repeated = theory_builder.run_build("A1\nA1,A1", settings, False, log)
        self.assertEqual((repeated["added"], repeated["existing"], repeated["errors"]), (0, 6, 0), logs)
        # Existing counts input candidates, not the number of unique DB rows.
        self.assertEqual(self.count("theories"), 5)
        self.assertEqual(self.count("lagrangian_realizations"), 5)
        self.assert_workers_disconnected()

    def test_group_pool_reuses_connections_across_many_input_lines(self):
        entries = [(line, ("A1",)) for line in range(1, 13)]
        events = []
        group_workers.process_groups(entries, 3, MYSQL_TEST_DATABASE, self.options,
                                     self.connection, True, events.append,
                                     lambda *_: None, lambda: False)
        counts = workers.Counts()
        owners = {}
        for event in events:
            counts.include(event.result.counts)
            owners.setdefault(event.line_number, set()).add(event.result.worker_pid)
        self.assertEqual((counts.candidates, counts.valid, counts.added, counts.existing),
                         (24, 24, 2, 22))
        self.assertEqual((counts.check_failed, counts.db_failed), (0, 0))
        self.assertEqual(len(owners), 12)
        self.assertTrue(all(len(pids) == 1 for pids in owners.values()))
        self.assertLessEqual(len(set.union(*owners.values())), 3)
        self.assertLessEqual(len(self.connection_ids), 3)
        self.assertEqual(self.count("theories"), 2)
        self.assert_workers_disconnected()

    def test_stop_group_build_keeps_all_committed_counts_and_skips_cache(self):
        settings = {f"mysql/{key}": value for key, value in self.options.items()}
        settings.update({"mysql/database": MYSQL_TEST_DATABASE, "index/full_max_order": 4,
                         "tools/processes": 3})
        stopped, logs = Event(), []

        def log(message, level="INFO"):
            logs.append((message, level))
            if "valid SCFT;" in message:
                stopped.set()

        with patch.object(cache, "build_decomposition_cache") as build:
            result = theory_builder.run_build("A1,A1\n" * 40, settings, True, log, stopped.is_set)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["errors"], 0, logs)
        self.assertGreater(result["added"], 0)
        self.assertEqual(self.count("theories"), result["added"])
        self.assertEqual(self.count("lagrangian_realizations"), result["added"])
        self.assertLessEqual(result["candidates"], 6 * 4)
        self.assertLessEqual(result["added"] + result["existing"], result["valid"])
        build.assert_not_called()
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

    def test_hundreds_of_distinct_candidates_are_all_inserted_in_parallel(self):
        candidates = [{"algebra": "A1", "hypermultiplets": [
            {"dynkin_labels": [1], "number": 4, "kind": "full"},
            {"dynkin_labels": [0], "number": n, "kind": "full"},
        ]} for n in range(1, 257)]
        results = []
        workers.process_candidates(candidates, "A1", 8, MYSQL_TEST_DATABASE,
                                   self.options, self.connection, False, results.append,
                                   lambda *args: None, lambda: False)
        counts = workers.Counts()
        for result in results:
            counts.include(result.counts)
        errors = [message for result in results for message, level in result.messages if level == "ERROR"]
        self.assertEqual(counts.db_failed, 0, errors[:5])
        self.assertEqual((counts.valid, counts.invalid, counts.added, counts.existing), (256, 0, 256, 0))
        self.assertEqual(counts.check_failed, 0)
        self.assertEqual(self.count("theories"), 256)
        self.assertEqual(self.count("theory_properties"), 256)
        self.assertEqual(self.count("lagrangian_realizations"), 256)
        self.assert_workers_disconnected()


if __name__ == "__main__":
    unittest.main()
