"""Isolated SCFT checks and imports, with bounded scheduling per gauge group.

Sage state and MySQL connections belong to individual spawned processes. Only
the coordinator receives results, combines counters/representations and logs.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, field
from multiprocessing import get_context
from multiprocessing.util import Finalize
import os


class BuildCancelled(Exception):
    pass


@dataclass
class Counts:
    candidates: int = 0
    valid: int = 0
    invalid: int = 0
    added: int = 0
    existing: int = 0
    check_failed: int = 0
    db_failed: int = 0

    def include(self, other):
        for key, value in asdict(other).items():
            setattr(self, key, getattr(self, key) + value)

    def describe(self):
        return (
            f"candidates={self.candidates}, valid SCFTs={self.valid}, "
            f"invalid={self.invalid}, added to DB={self.added}, "
            f"already in DB={self.existing}, check errors={self.check_failed}, "
            f"DB errors={self.db_failed}"
        )


@dataclass
class CandidateResult:
    counts: Counts = field(default_factory=Counts)
    representations: set = field(default_factory=set)
    messages: list = field(default_factory=list)
    worker_pid: int = field(default_factory=os.getpid)

    def log(self, message, level="INFO"):
        self.messages.append((message, level))

    def error(self, context, exc):
        self.log(f"{context}: {type(exc).__name__}: {exc}", "ERROR")


def theory_representations(anomaly):
    """Use actual factor orientations, including vectors and full-hyper duals."""
    from anomalies.lie_algebra import conjugate_dynkin_labels, named_representation_labels

    factors = anomaly.get("gauge_factors", [{"id": "gauge", "algebra": anomaly.get("algebra")}])
    result = set()
    for factor in factors:
        algebra = factor["algebra"]
        result.add((algebra, tuple(named_representation_labels(algebra, "adjoint"))))
        for hyper in anomaly["hypermultiplets"]:
            if not hyper.number:
                continue
            rep = (hyper.representations[factor["id"]]
                   if "gauge_factors" in anomaly else hyper.representation)
            result.add((algebra, tuple(rep.labels)))
            if hyper.kind == "full":
                result.add((algebra, tuple(conjugate_dynkin_labels(algebra, rep.labels))))
    return result


def rejection_reason(anomaly):
    reasons = list(anomaly["errors"])
    if not anomaly["anomaly_free"]:
        reasons.append("gauge anomalies do not cancel")
    for factor in anomaly.get("gauge_factors", [anomaly]):
        if not factor["one_loop_beta_vanishes"]:
            reasons.append(f"{factor['algebra']}: one-loop beta coefficient b0={factor['b0']}")
        if not factor["global_gauge_anomaly_free"]:
            reasons.append(f"{factor['algebra']}: Witten anomaly parity={factor['witten_anomaly_parity']}")
    return "; ".join(reasons) or "did not pass the Lagrangian SCFT-candidate checks"


def check_and_store(candidate, context, get_connection, collect_representations, cancelled):
    """Return plain data; never mutate coordinator state or write its logs."""
    from common.n2_theory_properties import calculate_n2_theory_properties
    from common.n2_theory_db import store_lagrangian_theory
    from anomalies.check_n2_anomalies import check_input_data

    result = CandidateResult()
    if cancelled():
        return result
    try:
        properties = calculate_n2_theory_properties(candidate)
        if not properties["lagrangian_scft_candidate"]:
            result.counts.invalid = 1
            result.log(f"{context}: INVALID — {rejection_reason(check_input_data(candidate))}", "WARNING")
            return result
    except ValueError as exc:
        result.counts.invalid = 1
        result.log(f"{context}: INVALID — {exc}", "WARNING")
        return result
    except Exception as exc:
        result.counts.check_failed = 1
        result.error(f"{context}, property check", exc)
        return result

    result.counts.valid = 1
    if collect_representations:
        try:
            result.representations.update(theory_representations(check_input_data(candidate)))
        except Exception as exc:
            result.error(f"{context}, collecting representations", exc)
    if cancelled():
        return result
    try:
        stored = store_lagrangian_theory(get_connection(), candidate, initialize_schema=False)
    except Exception as exc:
        result.counts.db_failed = 1
        result.error(f"{context}, database insert", exc)
    else:
        if stored.inserted:
            result.counts.added = 1
            outcome = "added to DB"
        else:
            result.counts.existing = 1
            outcome = "already in DB"
        result.log(f"{context}: valid SCFT; {outcome} (theory {stored.theory_id}).")
    return result


_worker_connection = None
_worker_database = None
_worker_options = None
_worker_stopped = None


def _close_worker_connection():
    global _worker_connection
    connection, _worker_connection = _worker_connection, None
    if connection is not None:
        connection.close()


def _initialize_worker(database, options, stopped):
    global _worker_database, _worker_options, _worker_stopped
    _worker_database, _worker_options, _worker_stopped = database, options, stopped
    # multiprocessing finalizers run on graceful ProcessPoolExecutor shutdown.
    Finalize(None, _close_worker_connection, exitpriority=10)


def _get_worker_connection():
    global _worker_connection
    if _worker_connection is None:
        from common.n2_theory_db import connect_database
        _worker_connection = connect_database(
            _worker_database, **_worker_options, initialize_schema=False,
        )
    return _worker_connection


def _candidate_task(candidate, context, collect_representations):
    result = check_and_store(
        candidate, context, _get_worker_connection, collect_representations,
        _worker_stopped.is_set,
    )
    if result.counts.db_failed:
        # A broken connection must not poison every later task in this worker.
        # Do not replay the failed candidate: its commit may be ambiguous.
        try:
            _close_worker_connection()
        except Exception as exc:
            result.error(f"{context}, closing worker database connection", exc)
    return result


def process_candidates(candidates, label, processes, database, options, connection,
                       collect_representations, report, log, cancelled):
    """Feed one candidate at a time to available workers, with a bounded queue.

    Groups have separate pools, capped at their candidate count. At most twice
    the worker count is submitted at once, so costly candidates cannot pin a
    large static partition to one worker. Stop drains completed/in-flight work
    before returning, preserving the counts of committed inserts.
    """
    if not candidates:
        return
    workers = min(processes, len(candidates))
    log(f"{label}: checking and storing candidates with {workers} worker processes.")

    def context(position):
        return f"{label}, candidate {position}/{len(candidates)}"

    if workers == 1:
        for position, candidate in enumerate(candidates, 1):
            if cancelled():
                raise BuildCancelled()
            report(check_and_store(candidate, context(position), lambda: connection,
                                   collect_representations, cancelled))
        if cancelled():
            raise BuildCancelled()
        return

    spawn = get_context("spawn")
    stopped = spawn.Event()
    pending = {}
    remaining = iter(enumerate(candidates, 1))
    pool_error = None
    with ProcessPoolExecutor(
        max_workers=workers, mp_context=spawn, initializer=_initialize_worker,
        initargs=(database, options, stopped),
    ) as executor:
        def submit_available():
            nonlocal pool_error
            while len(pending) < 2 * workers and not stopped.is_set():
                if cancelled():
                    stopped.set()
                    break
                item = next(remaining, None)
                if item is None:
                    break
                position, candidate = item
                try:
                    future = executor.submit(_candidate_task, candidate, context(position),
                                             collect_representations)
                except BrokenProcessPool as exc:
                    pool_error = exc
                    stopped.set()
                    break
                pending[future] = position

        try:
            submit_available()
            while pending:
                if cancelled():
                    stopped.set()
                if stopped.is_set():
                    for future in tuple(pending):
                        if future.cancel():
                            del pending[future]
                done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    position = pending.pop(future)
                    if future.cancelled():
                        continue
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = CandidateResult()
                        result.counts.check_failed = 1
                        result.error(f"{context(position)}, worker failure (database outcome may be unknown)", exc)
                        if isinstance(exc, BrokenProcessPool):
                            pool_error = exc
                            stopped.set()
                    report(result)
                submit_available()
        finally:
            # The executor waits for running calls; cooperative tasks observe
            # this event before checks and before beginning database imports.
            stopped.set()
            for future in pending:
                future.cancel()
    if cancelled():
        raise BuildCancelled()
    if pool_error is not None:
        raise RuntimeError(
            f"Candidate worker pool failed: {pool_error}; remaining candidates were not processed."
        ) from pool_error
