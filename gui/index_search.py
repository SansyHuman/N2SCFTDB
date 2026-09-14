"""Read missing-index jobs in a separate Sage process; never calculate or write."""

from __future__ import annotations

import json
import sys

from gui.logging_utils import make_log_record


def search_index_jobs(settings, emit_job, log):
    """Stream existing DB jobs with display groups; own and close the connection."""
    name = settings["mysql/database"].strip()
    if not name:
        raise ValueError("Set the MySQL database name in Settings → Preferences.")
    log("Loading the database backend…")
    from common import n2_theory_db as database
    from anomalies.lie_algebra import get_lie_algebra

    connection = database.connect_database(
        name, host=settings["mysql/host"], port=settings["mysql/port"],
        user=settings["mysql/user"], password=settings["mysql/password"],
        unix_socket=settings.get("mysql/unix_socket"),
        connect_timeout=settings["mysql/connect_timeout"], initialize_schema=False,
    )
    count = 0
    try:
        log("Searching for missing full indices, Coulomb indices or Coulomb spectra…")
        # Zero cutoffs are irrelevant in missing-only mode. A search must not
        # depend on calculation settings or silently select higher-order upgrades.
        for job in database.iter_lagrangian_index_jobs(
            connection, order=0, max_dimension=0, upgrade=False,
        ):
            data = job["input"]
            factors = ([data["algebra"]] if "algebra" in data else
                       [factor["algebra"] for factor in data["gauge_groups"]])
            algebras = [get_lie_algebra(factor) for factor in factors]
            # Preserve factor order, as the existing DB canonicalization does.
            group = {
                "algebras": [algebra.cartan_type for algebra in algebras],
                "label": " × ".join(algebra.group for algebra in algebras),
            }
            emit_job(group, job)
            count += 1
            if count % 100 == 0:
                log(f"Retrieved {count} theories…")
    finally:
        connection.close()
    return count


def main():
    password = ""
    try:
        settings = json.loads(sys.stdin.readline())["settings"]
        password = settings.get("mysql/password", "")

        def log(message):
            print(json.dumps(make_log_record(message, secrets=(password,))), flush=True)

        count = search_index_jobs(
            settings,
            lambda group, job: print(json.dumps({"group": group, "job": job}), flush=True),
            log,
        )
        # Only publish a complete snapshot after the DB connection closes.
        print(json.dumps({"complete": count}), flush=True)
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(json.dumps(make_log_record(message, "ERROR", secrets=(password,))), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
