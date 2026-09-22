"""Anomaly builds: one complete gauge-group enumeration per worker task."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from multiprocessing import get_context
from queue import Empty

from gui import candidate_workers as candidates
from gui.candidate_workers import BuildCancelled, CandidateResult


@dataclass
class GroupEvent:
    line_number: int
    factors: tuple
    phase: str
    result: CandidateResult


def _run_group(group, collect_representations, check, report, cancelled):
    """Enumerate locally and stream small results; candidate lists stay local."""
    from common.n2_theory_iter import (
        enumerate_simple_theory_candidates, enumerate_product_theory_candidates,
    )

    if cancelled():
        return
    line_number, factors = group
    label = ", ".join(factors)

    def emit(phase, result):
        report(GroupEvent(line_number, factors, phase, result))

    started = CandidateResult()
    started.log(f"Working on {label} (line {line_number}): enumerating candidates…")
    emit("started", started)
    phase = "enumeration"
    try:
        items = (enumerate_simple_theory_candidates(factors[0]) if len(factors) == 1
                 else enumerate_product_theory_candidates(factors))
        enumerated = CandidateResult()
        enumerated.counts.candidates = len(items)
        enumerated.log(f"{label} (line {line_number}): {len(items)} theory candidates.")
        emit("enumerated", enumerated)
        phase = "checking and storing candidates"
        for position, candidate in enumerate(items, 1):
            if cancelled():
                break
            context = f"{label} (line {line_number}), candidate {position}/{len(items)}"
            emit("candidate", check(candidate, context, collect_representations))
    except Exception as exc:
        failed = CandidateResult()
        failed.error(f"{label} (line {line_number}), {phase}", exc)
        emit("error", failed)
    finally:
        emit("finished", CandidateResult())


_worker_events = None


def _initialize_worker(database, options, stopped, events):
    global _worker_events
    candidates._initialize_worker(database, options, stopped)
    _worker_events = events


def _group_task(group, collect_representations):
    _run_group(group, collect_representations, candidates._candidate_task,
               _worker_events.put, candidates._worker_stopped.is_set)


def process_groups(groups, processes, database, options, connection,
                   collect_representations, report, log, cancelled):
    """Reuse one pool across groups, with at most twice its size outstanding.

    Only the coordinator calls ``report``/``log``. A synchronous manager queue
    streams progress during long groups and preserves acknowledged insert
    results even if a worker subsequently dies. It has no worker-side feeder
    thread to strand buffered results or block shutdown after cancellation.
    """
    if not groups:
        return
    if cancelled():
        raise BuildCancelled()
    workers = min(processes, len(groups))
    log(f"Processing {len(groups)} gauge groups with {workers} worker processes; "
        "each worker enumerates, checks and stores one complete group at a time.")
    if processes == 1:
        def check(candidate, context, collect):
            return candidates.check_and_store(candidate, context, lambda: connection,
                                              collect, cancelled)

        for group in groups:
            if cancelled():
                raise BuildCancelled()
            _run_group(group, collect_representations, check, report, cancelled)
        if cancelled():
            raise BuildCancelled()
        return

    spawn = get_context("spawn")
    stopped = spawn.Event()
    pending = {}
    remaining = iter(groups)
    pool_error = None
    with spawn.Manager() as manager:
        events = manager.Queue()

        def drain():
            while True:
                if cancelled():
                    stopped.set()
                try:
                    event = events.get_nowait()
                except Empty:
                    return
                report(event)

        with ProcessPoolExecutor(
            max_workers=workers, mp_context=spawn, initializer=_initialize_worker,
            initargs=(database, options, stopped, events),
        ) as executor:
            def submit_available():
                nonlocal pool_error
                while len(pending) < 2 * workers and not stopped.is_set():
                    if cancelled():
                        stopped.set()
                        break
                    group = next(remaining, None)
                    if group is None:
                        break
                    try:
                        future = executor.submit(_group_task, group, collect_representations)
                    except BrokenProcessPool as exc:
                        pool_error = exc
                        stopped.set()
                        break
                    pending[future] = group

            try:
                submit_available()
                while pending:
                    if cancelled():
                        stopped.set()
                    if stopped.is_set():
                        for future in tuple(pending):
                            if future.cancel():
                                del pending[future]
                    done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                    # Completed futures have synchronously published every event.
                    drain()
                    for future in done:
                        line_number, factors = pending.pop(future)
                        if future.cancelled():
                            continue
                        try:
                            future.result()
                        except Exception as exc:
                            failed = CandidateResult()
                            failed.error(
                                f"{', '.join(factors)} (line {line_number}), worker failure "
                                "(database outcome may be unknown; group will not be replayed)", exc,
                            )
                            report(GroupEvent(line_number, factors, "failed", failed))
                            if isinstance(exc, BrokenProcessPool):
                                pool_error = exc
                                stopped.set()
                    submit_available()
            finally:
                stopped.set()
                for future in pending:
                    future.cancel()
        drain()
    if pool_error is not None:
        raise RuntimeError(
            f"Gauge-group worker pool failed: {pool_error}; remaining groups were not processed."
        ) from pool_error
    if cancelled():
        raise BuildCancelled()
