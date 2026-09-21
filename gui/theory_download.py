"""Stream selected theory fields to CSV, with bounded parallel database readers.

This module deliberately needs neither Sage imports nor Qt. Workers write small
CSV chunks on disk; only counts cross process boundaries, never index payloads.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, TimeoutError
import csv
from dataclasses import dataclass
import io
import json
from multiprocessing import get_context
from multiprocessing.util import Finalize
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread

from gui.logging_utils import make_log_record


@dataclass(frozen=True)
class CsvField:
    name: str
    widget: str
    expression: str


CSV_FIELDS = (
    CsvField("theory_id", "searchCsvTheoryIdCheckBox", "t.id"),
    CsvField("name", "searchCsvTheoryNameCheckBox", "t.name"),
    CsvField("lagrangian_realization_id", "searchCsvRealizationIdCheckBox", "lr.id"),
    CsvField("gauge_group", "searchCsvGaugeGroupCheckBox", "lr.gauge_group"),
    CsvField("input_json", "searchCsvInputJsonCheckBox", "lr.input_json"),
    CsvField("flavor_symmetry", "searchCsvFlavorSymmetryCheckBox", "p.flavor_symmetry"),
    CsvField("flavor_rank", "searchCsvFlavorRankCheckBox", "p.flavor_rank"),
    CsvField("flavor_dimension", "searchCsvFlavorDimensionCheckBox", "p.flavor_dimension"),
    CsvField("conformal_manifold_dimension", "searchCsvConformalManifoldDimensionCheckBox", "p.conformal_manifold_dimension"),
    CsvField("central_charges_json", "searchCsvCentralChargesJsonCheckBox", "p.central_charges_json"),
    CsvField("central_charge_a_decimal", "searchCsvCentralChargeADecimalCheckBox", "p.central_charge_a_decimal"),
    CsvField("central_charge_c_decimal", "searchCsvCentralChargeCDecimalCheckBox", "p.central_charge_c_decimal"),
    CsvField("coulomb_branch_index_json", "searchCsvCoulombBranchIndexJsonCheckBox", "p.coulomb_branch_index_json"),
    CsvField("coulomb_branch_spectrum_json", "searchCsvCoulombBranchSpectrumJsonCheckBox", "p.coulomb_branch_spectrum_json"),
    CsvField("superconformal_index_json", "searchCsvSuperconformalIndexJsonCheckBox", "p.superconformal_index_json"),
    CsvField("superconformal_index_order", "searchCsvSuperconformalIndexOrderCheckBox", "p.superconformal_index_order"),
    CsvField("coulomb_branch_index_max_dimension_json", "searchCsvCoulombBranchIndexMaxDimensionJsonCheckBox", "p.coulomb_branch_index_max_dimension_json"),
    CsvField("disconnected_sector_count", "searchCsvDisconnectedSectorCountCheckBox", "p.disconnected_sector_count"),
    CsvField("disconnected_sectors_json", "searchCsvDisconnectedSectorsJsonCheckBox", "p.disconnected_sectors_json"),
)
BATCH_SIZE = 32
_connection = None
_settings = None
_fields = ()
_stopped = None


class DownloadCancelled(Exception):
    """Cooperative cancellation leaves the destination unchanged."""


def validate_request(theory_ids, field_names, settings):
    if (not isinstance(theory_ids, (list, tuple)) or not theory_ids
            or any(type(value) is not int or not 1 <= value <= 2**64 - 1 for value in theory_ids)
            or any(left >= right for left, right in zip(theory_ids, theory_ids[1:]))):
        raise ValueError("Search again: theory IDs must be positive, unique and sorted.")
    available = {field.name: field for field in CSV_FIELDS}
    if (not isinstance(field_names, (list, tuple)) or not field_names
            or any(not isinstance(name, str) or name not in available for name in field_names)
            or len(set(field_names)) != len(field_names)):
        raise ValueError("Select at least one valid CSV field, without duplicates.")
    if not settings["mysql/database"].strip():
        raise ValueError("Set the MySQL database name in Settings → Preferences.")
    cpus = settings.get("tools/processes", -1)
    if type(cpus) is not int or cpus == 0 or cpus < -1:
        raise ValueError("CPU core count must be -1 or positive.")
    cpus = (os.cpu_count() or 1) if cpus == -1 else cpus
    workers = min(cpus, (len(theory_ids) + BATCH_SIZE - 1) // BATCH_SIZE)
    return tuple(available[name] for name in field_names), workers


def build_query(theory_ids, fields):
    columns = ["t.id AS _theory_id"] + [f"{field.expression} AS {field.name}" for field in fields]
    return ("SELECT " + ", ".join(columns)
            + " FROM theories AS t LEFT JOIN lagrangian_realizations AS lr ON lr.theory_id = t.id"
            + " LEFT JOIN theory_properties AS p ON p.theory_id = t.id"
            + " WHERE t.id IN (" + ",".join("%s" for _ in theory_ids) + ") ORDER BY t.id, lr.id",
            tuple(theory_ids))


def _connect(settings):
    import pymysql
    return pymysql.connect(
        database=settings["mysql/database"].strip(), host=settings["mysql/host"],
        port=settings["mysql/port"], user=settings["mysql/user"], password=settings["mysql/password"],
        unix_socket=settings.get("mysql/unix_socket"), connect_timeout=settings["mysql/connect_timeout"],
        read_timeout=30, write_timeout=30, charset="utf8mb4", autocommit=True,
    )


def _close_connection():
    global _connection
    connection, _connection = _connection, None
    if connection is not None:
        connection.close()


def _initialize_worker(settings, fields, stopped):
    global _settings, _fields, _stopped
    _settings, _fields, _stopped = settings, fields, stopped
    Finalize(None, _close_connection, exitpriority=10)


def _write_chunk(theory_ids, filename, cancelled=lambda: False):
    global _connection
    from pymysql.cursors import SSDictCursor

    def check_cancelled():
        if _stopped.is_set() or cancelled():
            raise DownloadCancelled()

    check_cancelled()
    if _connection is None:
        _connection = _connect(_settings)
    seen, rows_written = [], 0
    try:
        with Path(filename).open("x", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            with _connection.cursor(SSDictCursor) as cursor:
                cursor.execute(*build_query(theory_ids, _fields))
                for row in cursor:
                    check_cancelled()
                    theory_id = int(row["_theory_id"])
                    if not seen or seen[-1] != theory_id:
                        seen.append(theory_id)
                    # csv.writer preserves Decimal/integer text and quotes JSON,
                    # commas and newlines correctly. SQL NULL becomes an empty cell.
                    writer.writerow([row[field.name] for field in _fields])
                    rows_written += 1
        if seen != list(theory_ids):
            raise RuntimeError("Some searched theories no longer exist. Search again before downloading.")
        return {"theories": len(seen), "rows": rows_written}
    except BaseException:
        _close_connection()
        raise


def download_theories(theory_ids, field_names, settings, destination, emit, log,
                      cancelled=lambda: False):
    """Write every realization for retained IDs, then atomically publish the CSV.

    Progress counts fully written theories, not CSV rows. Missing realizations
    produce one row with blank realization fields. Parallel chunks can observe
    external DB changes at different times; no search is rerun here.
    """
    fields, workers = validate_request(theory_ids, field_names, settings)
    target = Path(destination).expanduser().absolute()
    if not target.name or target.is_dir():
        raise ValueError("Choose a CSV file, not a directory.")
    total, written, rows_written = len(theory_ids), 0, 0
    log(f"Writing {total} theories with {workers} export process(es) to {target}.")
    spawn = get_context("spawn")
    stopped = spawn.Event()

    def check_cancelled():
        if cancelled():
            stopped.set()
            raise DownloadCancelled()

    check_cancelled()
    # Keep staging beside the destination so os.replace is atomic on its volume.
    with tempfile.TemporaryDirectory(prefix=".n2-csv-", dir=target.parent) as directory:
        root = Path(directory)
        staged = root / "complete.csv"
        jobs = iter((tuple(theory_ids[start:start + BATCH_SIZE]), root / f"chunk-{start}.csv")
                    for start in range(0, total, BATCH_SIZE))
        with staged.open("xb") as output:
            header = io.StringIO(newline="")
            csv.writer(header).writerow([field.name for field in fields])
            output.write(header.getvalue().encode("utf-8-sig"))

            def merge(job, result):
                nonlocal written, rows_written
                check_cancelled()
                ids, path = job
                if result["theories"] != len(ids):
                    raise RuntimeError("An export chunk did not contain every requested theory.")
                with path.open("rb") as source:
                    while block := source.read(1024 * 1024):
                        check_cancelled()
                        output.write(block)
                output.flush()
                path.unlink()
                written += len(ids)
                rows_written += result["rows"]
                # Reserve 100% until the complete CSV has been published.
                if written < total:
                    emit({"download_progress": {"written": written, "total": total}})

            if workers == 1:
                _initialize_worker(settings, fields, stopped)
                try:
                    for job in jobs:
                        merge(job, _write_chunk(*job, cancelled=cancelled))
                finally:
                    _close_connection()
            else:
                with ProcessPoolExecutor(max_workers=workers, mp_context=spawn,
                                         initializer=_initialize_worker,
                                         initargs=(settings, fields, stopped)) as pool:
                    pending = []

                    def submit_one():
                        job = next(jobs, None)
                        if job is not None:
                            pending.append((job, pool.submit(_write_chunk, *job)))

                    try:
                        for _ in range(workers):
                            submit_one()
                        while pending:
                            check_cancelled()
                            job, future = pending[0]
                            try:
                                result = future.result(timeout=0.1)
                            except TimeoutError:
                                continue
                            merge(job, result)
                            pending.pop(0)
                            submit_one()
                    finally:
                        stopped.set()
                        for _, future in pending:
                            future.cancel()
            check_cancelled()
            output.flush()
            os.fsync(output.fileno())
        check_cancelled()
        os.replace(staged, target)
    result = {"written": written, "total": total, "rows": rows_written, "path": str(target)}
    emit({"download_complete": result})
    return result


def main():
    password = ""

    def emit(record):
        print(json.dumps(record), flush=True)

    try:
        pending = b""
        while b"\n" not in pending:
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                raise ValueError("Missing CSV download request.")
            pending += chunk
        line, pending = pending.split(b"\n", 1)
        request = json.loads(line)
        password = request["settings"].get("mysql/password", "")
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
        download_theories(
            request["theory_ids"], request["fields"], request["settings"], request["destination"], emit,
            lambda message: emit(make_log_record(message, secrets=(password,))), stopped.is_set,
        )
        return 0
    except DownloadCancelled:
        emit({"download_cancelled": True})
        return 0
    except Exception as exc:
        emit(make_log_record(f"{type(exc).__name__}: {exc}", "ERROR", secrets=(password,)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
