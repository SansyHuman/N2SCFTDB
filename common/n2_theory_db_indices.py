#!/usr/bin/env python3
"""Fill missing N=2 theory indices, or upgrade them to higher requested cutoffs.

Run after importing anomaly-checked theories with n2_theory_db.py. Each theory
is visited through one stored Lagrangian realization. Calculations happen
outside database transactions; completed components are saved independently,
so a later failure can be retried without losing earlier successful work.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import pymysql

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import n2_theory_db as database
from common import n2_theory_properties as properties
from common.number_utils import as_nonnegative_int


def calculate_index_job(connection, job, *, order, max_dimension,
                        missing_only=False, recheck=False, full_index_options=None,
                        cancelled=lambda: False, log=lambda message, level="INFO": None):
    """Calculate a retained job outside transactions, committing each component.

    In missing-only mode or with ``recheck=True``, an ID lookup rechecks stale
    selections without another database search. Upgrade rechecks compare the
    current cutoffs and preserve unknown precision. Stop saves the active
    component, then returns the remaining fields so the caller can retry without
    discarding useful work.
    """
    result = {
        "theory_id": job["theory_id"],
        "lagrangian_realization_id": job["lagrangian_realization_id"],
        "updated_fields": [], "skipped_fields": {
            key: "unknown_precision" for key in job["unknown_precision"]
        }, "errors": {}, "remaining_fields": list(job["needed_fields"]),
    }
    if cancelled():
        return result
    if missing_only or recheck:
        if missing_only:
            needed = database.missing_lagrangian_index_fields(
                connection, job["lagrangian_realization_id"], theory_id=job["theory_id"],
            )
        else:
            needed = database.needed_lagrangian_index_fields(
                connection, job["lagrangian_realization_id"], theory_id=job["theory_id"],
                order=order, max_dimension=max_dimension,
            )
        for key in set(job["needed_fields"]) - set(needed):
            result["skipped_fields"][key] = "already_present" if missing_only else "not_needed_at_requested_cutoff"
        for key in needed:
            result["skipped_fields"].pop(key, None)
        result["remaining_fields"] = needed
    for key in tuple(result["remaining_fields"]):
        if cancelled():
            break
        log(f"Theory {job['theory_id']}: calculating {key}…")
        saving = False
        try:
            if key == "superconformal_index":
                options = dict(full_index_options or {})
                options.setdefault("theory_db_connection", connection)
                value = properties.calculate_superconformal_index(
                    job["input"], order=order, **options,
                )
                payload = {key: str(value), "superconformal_index_order": order}
            elif key == "coulomb_branch_index":
                value = properties.calculate_coulomb_branch_index(job["input"], max_dimension=max_dimension)
                payload = {key: str(value), "coulomb_branch_index_max_dimension": max_dimension}
            elif key == "coulomb_branch_spectrum":
                value = properties.calculate_coulomb_branch_spectrum(job["input"])
                payload = {key: value}
            else:
                raise ValueError(f"unknown index component {key}")
            if value is None:
                raise ValueError("stored input no longer passes the SCFT-candidate checks")
            saving = True
            saved = database.update_lagrangian_indices(
                connection, job["lagrangian_realization_id"], payload, missing_only=missing_only,
            )
            result["updated_fields"].extend(saved["updated_fields"])
            result["skipped_fields"].update(saved["skipped_fields"])
            result["remaining_fields"].remove(key)
            log(f"Theory {job['theory_id']}: {key} "
                + ("saved." if key in saved["updated_fields"] else "already stored; preserved."))
        except (OSError, ValueError, ArithmeticError, RuntimeError, pymysql.MySQLError) as exc:
            result["errors"][key] = str(exc)
            log(f"Theory {job['theory_id']}: {key}: {exc}", level="ERROR")
            if saving and not isinstance(exc, ValueError):
                # Never reuse/replay an uncertain connection or commit.
                break
    return result


def fill_lagrangian_indices(
    connection, *, order: Any | None = None, max_dimension: Any | None = None,
    upgrade: bool = False, limit: int | None = None,
):
    """Yield an outcome per theory; fill missing components and optionally upgrade.

    Equal/lower precision and legacy unknown precision never replace stored
    indices. The limit bounds visited jobs; no persistent cursor is needed to
    resume because selection uses the current missing fields/cutoffs. Failures
    are reported per component and subsequent components/theories continue.
    The connection must have an initialized schema and no open transaction.
    """
    order = database._exact_cutoff(
        properties.INDEX_MAX_ORDER if order is None else order, full_index=True,
    )
    max_dimension = database._exact_cutoff(
        properties.C_INDEX_MAX_ORDER if max_dimension is None else max_dimension,
    )
    if limit is not None:
        limit = as_nonnegative_int(limit, "limit")
    if limit == 0:
        return
    jobs = database.iter_lagrangian_index_jobs(
        connection, order=order, max_dimension=max_dimension, upgrade=upgrade,
    )
    for count, job in enumerate(jobs, 1):
        result = calculate_index_job(connection, job, order=order, max_dimension=max_dimension)
        # Keep the established CLI/API result shape.
        result.pop("remaining_fields")
        yield result
        if limit is not None and count >= limit:
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="name of an existing MySQL database")
    parser.add_argument("--host", default=os.environ.get("N2_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default=os.environ.get("N2_DB_USER", "root"))
    parser.add_argument("--unix-socket", default=os.environ.get("N2_DB_UNIX_SOCKET"))
    parser.add_argument("--connect-timeout", type=int, default=10)
    parser.add_argument("--index-order", type=int, default=properties.INDEX_MAX_ORDER)
    parser.add_argument("--coulomb-max-dimension", default=str(properties.C_INDEX_MAX_ORDER))
    parser.add_argument("--upgrade", action="store_true", help="also upgrade indices with known lower cutoffs")
    parser.add_argument("--limit", type=int, help="maximum number of theory jobs to visit")
    parser.add_argument(
        "--char-cache-database", type=Path,
        help="character SQLite cache file (default: project-root char_decomposition_cache.db)",
    )
    parser.add_argument("--lie-executable", default=properties.LIE_EXECUTABLE)
    parser.add_argument("--form-executable", default=properties.FORM_EXECUTABLE)
    parser.add_argument("--tform-executable", default=properties.TFORM_EXECUTABLE)
    parser.add_argument("--form-threads", type=int, default=properties.FORM_THREADS,
                        help="1 uses FORM; larger values use TFORM with this many workers")
    parser.add_argument("--timeout", type=float, default=properties.DEFAULT_TIMEOUT)
    parser.add_argument("--processes", type=int, default=properties.DEFAULT_PROCESS_COUNT)
    args = parser.parse_args(argv)
    connection = None
    processed = failed = 0
    try:
        # Reject invalid options before connecting, which may migrate a schema.
        order = database._exact_cutoff(args.index_order, full_index=True)
        maximum = database._exact_cutoff(args.coulomb_max_dimension)
        if args.limit is not None:
            as_nonnegative_int(args.limit, "limit")
        if args.processes < 1 or args.timeout <= 0:
            raise ValueError("processes and timeout must be positive")
        from common.form_utils import validate_form_threads
        form_threads = validate_form_threads(args.form_threads)
        if args.char_cache_database is not None:
            properties.CHAR_CACHE_DATABASE_PATH = args.char_cache_database
        properties.LIE_EXECUTABLE = args.lie_executable
        properties.FORM_EXECUTABLE = args.form_executable
        properties.TFORM_EXECUTABLE = args.tform_executable
        properties.FORM_THREADS = form_threads
        properties.DEFAULT_TIMEOUT = args.timeout
        properties.DEFAULT_PROCESS_COUNT = args.processes
        connection = database.connect_database(
            args.database, host=args.host, port=args.port, user=args.user,
            password=os.environ.get("N2_DB_PASSWORD", ""),
            unix_socket=args.unix_socket, connect_timeout=args.connect_timeout,
        )
        for result in fill_lagrangian_indices(
            connection, order=order, max_dimension=maximum,
            upgrade=args.upgrade, limit=args.limit,
        ):
            processed += 1
            failed += bool(result["errors"])
            print(json.dumps(result), flush=True)
    except (OSError, ValueError, ArithmeticError, RuntimeError, pymysql.MySQLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps({"processed": processed, "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
