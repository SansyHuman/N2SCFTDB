"""Sage coordinator and spawned index workers for the retained GUI selection."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
import json
import math
from multiprocessing import get_context
from multiprocessing.util import Finalize
import os
from queue import Empty
import sys
from threading import Event, Thread

from gui.logging_utils import make_log_record


_connection = None
_settings = None
_stopped = None
_messages = None


def _close_connection():
    global _connection
    connection, _connection = _connection, None
    if connection is not None:
        connection.close()


def _initialize_worker(settings, stopped, messages):
    global _settings, _stopped, _messages
    _settings, _stopped, _messages = settings, stopped, messages
    Finalize(None, _close_connection, exitpriority=10)


def _worker_log(message, level="INFO"):
    _messages.put(make_log_record(message, level, secrets=(_settings["mysql/password"],)))


def _calculate_job(job):
    global _connection
    from common import n2_theory_db as database
    from common import n2_theory_properties as properties
    from common.n2_theory_db_indices import calculate_index_job

    try:
        if _connection is None and not _stopped.is_set():
            options = {key: _settings[f"mysql/{key}"] for key in
                       ("host", "port", "user", "password", "connect_timeout")}
            _connection = database.connect_database(
                _settings["mysql/database"], **options,
                unix_socket=_settings.get("mysql/unix_socket"), initialize_schema=False,
            )
        # These globals are private to this spawned process. The full-index
        # cache runs serially inside each theory worker, avoiding nested pools.
        properties.FORM_EXECUTABLE = _settings["tools/form_executable"]
        properties.DEFAULT_TIMEOUT = _settings["tools/timeout"]
        result = calculate_index_job(
            _connection, job, order=_settings["index/full_max_order"],
            max_dimension=_settings["index/coulomb_max_dimension"], missing_only=True,
            full_index_options={
                "database_path": _settings["cache/character_database"],
                "form_cache_database_path": _settings["cache/form_database"],
                "lie_executable": _settings["tools/lie_executable"],
                "form_executable": _settings["tools/form_executable"],
                "timeout": _settings["tools/timeout"], "processes": 1,
            }, cancelled=_stopped.is_set, log=_worker_log,
        )
    except Exception as exc:
        result = _failed_job(job, exc)
        _worker_log(f"Theory {job['theory_id']}: {type(exc).__name__}: {exc}", "ERROR")
    if result["errors"]:
        # Failed connections/rollbacks must not poison later jobs. Never replay
        # a job automatically when a connection or commit outcome is uncertain.
        try:
            _close_connection()
        except Exception as exc:
            _worker_log(f"Closing index connection: {exc}", "ERROR")
    result["worker_pid"] = os.getpid()
    return result


def _failed_job(job, exc):
    return {
        "theory_id": job["theory_id"],
        "lagrangian_realization_id": job["lagrangian_realization_id"],
        "updated_fields": [], "skipped_fields": {},
        "remaining_fields": list(job["needed_fields"]),
        "errors": {"worker": f"{type(exc).__name__}: {exc}"},
    }


def _validate_request(jobs, settings):
    from common.n2_theory_db import _exact_cutoff
    from common.number_utils import as_integer

    if not jobs:
        raise ValueError("Select at least one gauge group with missing indices.")
    seen = set()
    fields = {"superconformal_index", "coulomb_branch_index", "coulomb_branch_spectrum"}
    for job in jobs:
        for key in ("theory_id", "lagrangian_realization_id"):
            if type(job[key]) is not int or job[key] <= 0:
                raise ValueError(f"invalid retained {key}")
        if job["theory_id"] in seen or not isinstance(job["input"], dict):
            raise ValueError("duplicate or invalid retained theory")
        if not job["needed_fields"] or not set(job["needed_fields"]) <= fields:
            raise ValueError("invalid retained index fields")
        seen.add(job["theory_id"])
    settings = dict(settings)
    settings["mysql/database"] = settings["mysql/database"].strip()
    if not settings["mysql/database"]:
        raise ValueError("Set the MySQL database name in Settings → Preferences.")
    settings["index/full_max_order"] = _exact_cutoff(settings["index/full_max_order"], full_index=True)
    settings["index/coulomb_max_dimension"] = _exact_cutoff(settings["index/coulomb_max_dimension"])
    if not math.isfinite(settings["tools/timeout"]) or settings["tools/timeout"] <= 0:
        raise ValueError("LiE / FORM timeout must be positive and finite")
    processes = as_integer(settings.get("tools/processes", -1), "CPU core count")
    if processes == -1:
        processes = os.cpu_count() or 1
    elif processes <= 0:
        raise ValueError("CPU core count must be -1 (all system cores) or positive")
    return settings, min(processes, len(jobs))


def run_calculation(jobs, settings, emit, log, cancelled=lambda: False):
    """Dynamically schedule every selected theory, at most 2*workers outstanding.

    Only completed job reports remove work from the GUI snapshot. A lost worker
    leaves its job retained; a later explicit retry rechecks current DB fields.
    No schema migration or database-wide search occurs here.
    """
    processed = failed = 0
    status = "completed"
    try:
        settings, workers = _validate_request(jobs, settings)
        log(f"Calculating {len(jobs)} selected theories with {workers} worker processes; "
            f"full order={settings['index/full_max_order']}, "
            f"Coulomb cutoff={settings['index/coulomb_max_dimension']}.")
        spawn = get_context("spawn")
        stopped = spawn.Event()
        if cancelled():
            stopped.set()
        remaining = iter(jobs)
        pending = {}
        pool_error = None
        # Manager queues use synchronous transfers, with no child feeder thread
        # that can deadlock executor shutdown while its pipe waits to be drained.
        with spawn.Manager() as manager:
            messages = manager.Queue()

            def drain_logs():
                while True:
                    try:
                        emit(messages.get_nowait())
                    except Empty:
                        return

            with ProcessPoolExecutor(
                max_workers=workers, mp_context=spawn, initializer=_initialize_worker,
                initargs=(settings, stopped, messages),
            ) as pool:
                def submit_available():
                    nonlocal pool_error
                    while len(pending) < 2 * workers and not stopped.is_set():
                        if cancelled():
                            stopped.set()
                            break
                        job = next(remaining, None)
                        if job is None:
                            break
                        try:
                            pending[pool.submit(_calculate_job, job)] = job
                        except BrokenProcessPool as exc:
                            pool_error = exc
                            stopped.set()
                            break

                try:
                    submit_available()
                    while pending:
                        if cancelled():
                            stopped.set()
                        if stopped.is_set():
                            for future in tuple(pending):
                                if future.cancel():
                                    del pending[future]
                        drain_logs()
                        done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                        for future in done:
                            job = pending.pop(future)
                            try:
                                result = future.result()
                            except Exception as exc:
                                result = _failed_job(job, exc)
                                log(f"Theory {job['theory_id']}: worker failed; database outcome may "
                                    f"be unknown: {exc}", "ERROR")
                                if isinstance(exc, BrokenProcessPool):
                                    pool_error = exc
                                    stopped.set()
                            processed += 1
                            failed += bool(result["errors"])
                            # Result diagnostics travel over stdin/stdout only,
                            # but redact them just like ordinary worker logs.
                            result["errors"] = {
                                key: make_log_record(value, secrets=(settings["mysql/password"],))["log"]
                                for key, value in result["errors"].items()
                            }
                            emit({"result": result})
                            log(f"Progress: {processed}/{len(jobs)} theories returned; "
                                f"{failed} with errors.")
                        submit_available()
                finally:
                    stopped.set()
                    for future in pending:
                        future.cancel()
            drain_logs()
        if pool_error is not None:
            raise RuntimeError(f"Index worker pool failed: {pool_error}")
        status = "stopped" if cancelled() else "completed with errors" if failed else "completed"
    except Exception as exc:
        status = "failed"
        log(f"Index calculation failed: {type(exc).__name__}: {exc}", "ERROR")
    summary = {"status": status, "processed": processed, "failed": failed, "total": len(jobs)}
    log(f"Index calculation {status}: {processed}/{len(jobs)} theories returned, "
        f"{failed} with errors. Completed components are saved; unfinished jobs are retained.",
        "ERROR" if status == "failed" else "WARNING" if failed else "INFO")
    emit({"calculation_complete": summary})
    return summary


def main():
    pending = b""
    while b"\n" not in pending:
        chunk = os.read(sys.stdin.fileno(), 65536)
        if not chunk:
            raise ValueError("Missing index request on stdin")
        pending += chunk
    request_line, pending = pending.split(b"\n", 1)
    request = json.loads(request_line)
    stopped = Event()

    def watch_input():
        buffer = pending
        while True:
            lines = buffer.split(b"\n")
            buffer = lines.pop()
            if any(line.strip() == b"stop" for line in lines):
                stopped.set()
                return
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                stopped.set()
                return
            buffer += chunk

    Thread(target=watch_input, daemon=True).start()

    def emit(record):
        print(json.dumps(record), flush=True)

    def log(message, level="INFO"):
        emit(make_log_record(message, level, secrets=(request["settings"].get("mysql/password", ""),)))

    summary = run_calculation(request["jobs"], request["settings"], emit, log, stopped.is_set)
    return int(summary["status"] in ("failed", "completed with errors"))


if __name__ == "__main__":
    raise SystemExit(main())
