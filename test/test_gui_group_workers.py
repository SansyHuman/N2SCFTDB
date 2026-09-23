"""Group ownership, streaming, bounded scheduling and spawned failure checks."""

from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import os
from queue import Queue
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from common import n2_theory_iter as theories
from gui import group_workers as groups
from gui.candidate_workers import BuildCancelled, CandidateResult, Counts


def _fake_check(candidate, context, collect):
    result = CandidateResult()
    result.counts.valid = result.counts.added = 1
    return result


def _initialize_without_database(database, options, stopped, events):
    groups._initialize_worker(database, options, stopped, events)
    groups.candidates._candidate_task = _fake_check


def _exit_after_reporting(group, collect):
    result = CandidateResult()
    result.counts.valid = result.counts.added = 1
    groups._worker_events.put(groups.GroupEvent(*group, "candidate", result))
    os._exit(17)


class GroupSchedulingTests(unittest.TestCase):
    def run_threads(self, entries, task, report, cancelled=lambda: False, processes=3):
        state = SimpleNamespace(stopped=Event(), events=Queue(), submissions=[])
        manager = MagicMock()
        manager.__enter__.return_value = manager
        manager.Queue.return_value = state.events
        spawn = SimpleNamespace(Event=lambda: state.stopped, Manager=lambda: manager)

        def executor(**kwargs):
            state.workers = kwargs["max_workers"]
            self.assertIs(kwargs["initializer"], groups._initialize_worker)
            pool = ThreadPoolExecutor(max_workers=state.workers)
            submit = pool.submit

            def tracked_submit(fn, *args):
                state.submissions.append(args)
                return submit(fn, *args)

            pool.submit = tracked_submit
            return pool

        with patch.object(groups, "get_context", return_value=spawn), \
             patch.object(groups, "ProcessPoolExecutor", side_effect=executor) as factory, \
             patch.object(groups, "_group_task", side_effect=lambda *args: task(state, *args)):
            try:
                groups.process_groups(entries, processes, "unused", {}, None, True,
                                      report, lambda *_: None, cancelled)
            finally:
                self.state = state
                self.assertEqual(factory.call_count, 1)
        return state

    def test_groups_enumerate_and_check_on_one_worker_with_streamed_progress(self):
        coordinator = get_ident()
        barrier, progress_received, lock = Barrier(3), Event(), Lock()
        owners, checks, events = {}, {}, []
        entries = [(i, (f"A{i}",)) for i in range(1, 13)]

        def enumerate_group(algebra):
            with lock:
                owners[algebra] = get_ident()
            if int(algebra[1:]) <= 3:
                barrier.wait(timeout=10)
            return [(algebra, position) for position in range(3)]

        def check(candidate, context, collect):
            algebra, position = candidate
            self.assertEqual(get_ident(), owners[algebra])
            with lock:
                checks.setdefault(algebra, []).append(position)
            if algebra == "A1" and position == 1:
                # This group cannot finish until the coordinator receives its
                # first candidate; returning progress only at group end hangs.
                self.assertTrue(progress_received.wait(timeout=10))
            return _fake_check(candidate, context, collect)

        def task(state, entry, collect):
            groups._run_group(entry, collect, check, state.events.put, state.stopped.is_set)

        def report(event):
            self.assertEqual(get_ident(), coordinator)
            events.append(event)
            if event.line_number == 1 and event.phase == "candidate":
                progress_received.set()

        with patch.object(theories, "enumerate_simple_theory_candidates", side_effect=enumerate_group):
            state = self.run_threads(entries, task, report)
        self.assertEqual(state.workers, 3)
        self.assertEqual([args[0] for args in state.submissions], entries)
        self.assertEqual(len(set(owners.values())), 3)
        self.assertNotIn(coordinator, owners.values())
        self.assertEqual(checks, {f"A{i}": [0, 1, 2] for i in range(1, 13)})
        self.assertEqual(sum(event.result.counts.added for event in events), 36)
        self.assertFalse(any(event.result.messages for event in events if event.phase == "error"))

    def test_stop_bounds_submissions_and_drains_all_reported_commits(self):
        stopped, barrier, events = Event(), Barrier(3), []

        def task(state, entry, collect):
            if state.stopped.is_set():
                return
            barrier.wait(timeout=10)
            result = _fake_check(None, None, collect)
            state.events.put(groups.GroupEvent(*entry, "candidate", result))
            self.assertTrue(state.stopped.wait(timeout=10))
            state.events.put(groups.GroupEvent(*entry, "finished", CandidateResult()))

        def report(event):
            events.append(event)
            if event.result.counts.added:
                stopped.set()

        with self.assertRaises(BuildCancelled):
            self.run_threads([(i, ("A1",)) for i in range(100)], task, report, stopped.is_set)
        self.assertLessEqual(len(self.state.submissions), 6)
        self.assertEqual(sum(event.result.counts.added for event in events), 3)
        self.assertEqual(sum(event.phase == "finished" for event in events), 3)

    def test_submit_failure_still_drains_completed_group_progress(self):
        events, reported = Queue(), []
        manager = MagicMock()
        manager.__enter__.return_value = manager
        manager.Queue.return_value = events
        spawn = SimpleNamespace(Event=Event, Manager=lambda: manager)
        executor = MagicMock()
        executor.__enter__.return_value = executor
        submitted = []

        def submit(fn, entry, collect):
            submitted.append(entry)
            if len(submitted) == 2:
                raise BrokenProcessPool("synthetic submission failure")
            events.put(groups.GroupEvent(*entry, "candidate", _fake_check(None, None, collect)))
            future = Future()
            future.set_result(None)
            return future

        executor.submit.side_effect = submit
        with patch.object(groups, "get_context", return_value=spawn), \
             patch.object(groups, "ProcessPoolExecutor", return_value=executor):
            with self.assertRaisesRegex(RuntimeError, "remaining groups were not processed"):
                groups.process_groups([(i, ("A1",)) for i in range(10)], 2, "unused", {},
                                      None, False, reported.append, lambda *_: None, lambda: False)
        self.assertEqual(sum(event.result.counts.added for event in reported), 1)
        self.assertEqual(len(submitted), 2)

    def test_real_spawn_keeps_enumeration_and_checks_in_one_process_per_group(self):
        events = []
        with patch.object(groups, "_initialize_worker", _initialize_without_database):
            # Only one input group still belongs to a spawned worker when the
            # requested CPU count is greater than one.
            groups.process_groups([(1, ("A1", "A1"))], 4, "unused", {}, None, False,
                                  events.append, lambda *_: None, lambda: False)
        pids = {event.result.worker_pid for event in events}
        self.assertEqual(len(pids), 1)
        self.assertNotIn(os.getpid(), pids)
        totals = Counts()
        for event in events:
            totals.include(event.result.counts)
        self.assertEqual((totals.candidates, totals.valid, totals.added), (4, 4, 4))
        self.assertEqual(events[0].phase, "started")
        self.assertEqual(events[-1].phase, "finished")

    def test_dead_spawned_worker_preserves_sent_results_without_replaying_group(self):
        events = []
        with patch.object(groups, "_group_task", _exit_after_reporting):
            with self.assertRaisesRegex(RuntimeError, "Gauge-group worker pool failed"):
                groups.process_groups([(i, ("A1",)) for i in range(100)], 2, "unused", {},
                                      None, False, events.append, lambda *_: None, lambda: False)
        committed = sum(event.result.counts.added for event in events)
        self.assertGreaterEqual(committed, 1)
        self.assertLessEqual(committed, 2)
        failures = [event for event in events if event.phase == "failed"]
        self.assertLessEqual(len(failures), 4)
        self.assertTrue(any("database outcome may be unknown" in message
                            for event in failures for message, _ in event.result.messages))


if __name__ == "__main__":
    unittest.main()
