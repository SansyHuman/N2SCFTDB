"""Anomaly-tab build worker, run in a separate Sage Python process.

The first stdin line is a JSON request. Later ``stop`` lines (or EOF) request
cooperative cancellation. Stdout carries flushed JSON log messages only.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread

from common.number_utils import as_integer, as_nonnegative_int
from gui.candidate_workers import (
    BuildCancelled, Counts, process_candidates, theory_representations,
)


def run_build(text, settings, build_cache, log, cancelled=lambda: False):
    """Build theories, reporting through ``log(message, level="INFO")``."""
    counts = Counts()
    representations = set()
    errors = 0
    cache_built = 0
    connection = None
    status = "completed"

    def check_stop():
        if cancelled():
            raise BuildCancelled()

    def error(context, exc):
        nonlocal errors
        errors += 1
        log(f"{context}: {type(exc).__name__}: {exc}", level="ERROR")

    try:
        rows = [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
        if not rows:
            raise ValueError("Enter at least one gauge group in the left area.")
        database_name = settings["mysql/database"].strip()
        if not database_name:
            raise ValueError("Set the MySQL database name in Settings → Preferences.")
        max_adams = as_nonnegative_int(settings["index/full_max_order"], "full index order") // 2
        processes = as_integer(settings.get("tools/processes", -1), "CPU core count")
        if processes == -1:
            processes = os.cpu_count() or 1
        elif processes <= 0:
            raise ValueError("CPU core count must be -1 (all system cores) or a positive integer.")
        check_stop()
        log("Loading Sage and the theory-building backend…")
        from common.n2_theory_iter import (
            enumerate_simple_theory_candidates, enumerate_product_theory_candidates,
        )
        from common.n2_theory_db import connect_database
        from anomalies.lie_algebra import get_lie_algebra
        from index.char_decomposition_cache import build_decomposition_cache

        groups = []
        for line_number, line in rows:
            check_stop()
            try:
                factors = [part.strip() for part in line.split(",")]
                if any(not factor for factor in factors):
                    raise ValueError("Empty gauge factor; separate nonempty Cartan types with commas.")
                groups.append((line_number, tuple(get_lie_algebra(f).cartan_type for f in factors)))
            except Exception as exc:
                error(f"line {line_number} ({line})", exc)
        if not groups:
            raise ValueError("No usable gauge groups were supplied.")

        check_stop()
        log(f"Connecting to MySQL database {database_name}…")
        connection_options = {key: settings[f"mysql/{key}"] for key in
                              ("host", "port", "user", "password", "connect_timeout")}
        if settings.get("mysql/unix_socket"):
            connection_options["unix_socket"] = settings["mysql/unix_socket"]
        # Complete all DDL before workers open their own DML-only connections.
        connection = connect_database(database_name, **connection_options)
        for line_number, factors in groups:
            check_stop()
            label = ", ".join(factors)
            log(f"Working on {label} (line {line_number}): enumerating candidates…")
            group_counts = Counts()
            phase = "enumeration"
            try:
                candidates = (enumerate_simple_theory_candidates(factors[0])
                              if len(factors) == 1 else enumerate_product_theory_candidates(factors))
                group_counts.candidates = len(candidates)
                log(f"{label}: {len(candidates)} theory candidates.")
                phase = "checking and storing candidates"

                def report(result):
                    nonlocal errors
                    group_counts.include(result.counts)
                    representations.update(result.representations)
                    for message, level in result.messages:
                        errors += level == "ERROR"
                        log(message, level=level)
                    if result.counts.valid or result.counts.invalid or result.counts.check_failed:
                        log(f"{label} progress: {group_counts.describe()}")

                process_candidates(
                    candidates, label, processes, database_name, connection_options,
                    connection, bool(build_cache and max_adams), report, log, cancelled,
                )
            except BuildCancelled:
                raise
            except Exception as exc:
                error(f"{label}, {phase}", exc)
            finally:
                counts.include(group_counts)
                log(f"{label} summary: {group_counts.describe()}")

        check_stop()
        if build_cache:
            if not max_adams:
                log("Character cache skipped: full index order // 2 is zero.")
            else:
                log(f"Building character cache for {len(representations)} distinct representations "
                    f"through Adams order {max_adams}, using up to {processes} worker processes.")
                for algebra, labels in sorted(representations):
                    check_stop()
                    context = f"{algebra} {labels}"
                    log(f"Character cache: {context}…")

                    def progress(order, total, computed):
                        log(f"Cache {context}: Adams order {order}/{max_adams}, "
                            f"products={total}, computed={computed}, reused={total - computed}.")
                        check_stop()

                    try:
                        build_decomposition_cache(
                            algebra, labels, max_adams,
                            database_path=Path(settings["cache/character_database"]).expanduser(),
                            lie_executable=settings["tools/lie_executable"],
                            timeout=settings["tools/timeout"], processes=processes, progress=progress,
                        )
                        cache_built += 1
                    except BuildCancelled:
                        raise
                    except Exception as exc:
                        error(f"character cache {context}", exc)
        if errors:
            status = "completed with errors"
    except BuildCancelled:
        status = "stopped"
        log("Stop requested; completed database and cache writes have been kept.")
    except Exception as exc:
        status = "failed"
        error("build", exc)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                error("closing database connection", exc)
                if status == "completed":
                    status = "completed with errors"
    summary_level = "ERROR" if status == "failed" else "WARNING" if errors else "INFO"
    log(f"Build {status}. Total: {counts.describe()}; errors={errors}; "
        f"cached representations={cache_built}/{len(representations)}.", level=summary_level)
    return dict(status=status, **asdict(counts), errors=errors,
                cache_built=cache_built, cache_total=len(representations))


def main():
    pending = b""
    while b"\n" not in pending:
        chunk = os.read(sys.stdin.fileno(), 65536)
        if not chunk:
            raise ValueError("Missing build request on stdin")
        pending += chunk
    request_line, pending = pending.split(b"\n", 1)
    request = json.loads(request_line)
    password = request["settings"].get("mysql/password", "")
    stopped = Event()

    def watch_input():
        # Raw reads avoid holding stdin's buffered-I/O lock during shutdown.
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

    def log(message, level="INFO"):
        if password:
            message = message.replace(password, "[redacted]")
        print(json.dumps({
            "log": message, "level": level,
            "timestamp": datetime.now().astimezone().isoformat(sep=" ", timespec="milliseconds"),
        }, ensure_ascii=True), flush=True)

    result = run_build(request["text"], request["settings"], request["build_cache"], log, stopped.is_set)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
