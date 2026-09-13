"""Anomaly-tab build worker, run in a separate Sage Python process.

The first stdin line is a JSON request. Later ``stop`` lines (or EOF) request
cooperative cancellation. Stdout carries flushed JSON log messages only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread

from common.number_utils import as_nonnegative_int


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


def theory_representations(anomaly):
    """Use actual factor orientations, including vectors and full-hyper duals."""
    from anomalies.lie_algebra import (
        conjugate_dynkin_labels, named_representation_labels,
    )

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


def run_build(text, settings, build_cache, log, cancelled=lambda: False):
    """Enumerate, check, store, then prebuild the union of valid representations."""
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
        log(f"ERROR — {context}: {type(exc).__name__}: {exc}")

    try:
        rows = [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
        if not rows:
            raise ValueError("Enter at least one gauge group in the left area.")
        database_name = settings["mysql/database"].strip()
        if not database_name:
            raise ValueError("Set the MySQL database name in Settings → Preferences.")
        max_adams = as_nonnegative_int(settings["index/full_max_order"], "full index order") // 2
        check_stop()
        log("Loading Sage and the theory-building backend…")
        from common.n2_theory_iter import (
            enumerate_simple_theory_candidates, enumerate_product_theory_candidates,
        )
        from common.n2_theory_properties import calculate_n2_theory_properties
        from common.n2_theory_db import connect_database, store_lagrangian_theory
        from anomalies.check_n2_anomalies import check_input_data
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
        connection = connect_database(
            database_name,
            **{key: settings[f"mysql/{key}"] for key in
               ("host", "port", "user", "password", "connect_timeout")},
        )
        for line_number, factors in groups:
            check_stop()
            label = ", ".join(factors)
            log(f"Working on {label} (line {line_number}): enumerating candidates…")
            group_counts = Counts()
            try:
                candidates = (enumerate_simple_theory_candidates(factors[0])
                              if len(factors) == 1 else enumerate_product_theory_candidates(factors))
                group_counts.candidates = len(candidates)
                log(f"{label}: {len(candidates)} theory candidates.")
                for position, candidate in enumerate(candidates, 1):
                    check_stop()
                    context = f"{label}, candidate {position}/{len(candidates)}"
                    try:
                        properties = calculate_n2_theory_properties(candidate)
                        if not properties["lagrangian_scft_candidate"]:
                            group_counts.invalid += 1
                            log(f"{context}: INVALID — {rejection_reason(check_input_data(candidate))}")
                            continue
                    except ValueError as exc:
                        group_counts.invalid += 1
                        log(f"{context}: INVALID — {exc}")
                        continue
                    except Exception as exc:
                        group_counts.check_failed += 1
                        error(f"{context}, property check", exc)
                        continue

                    group_counts.valid += 1
                    if build_cache and max_adams:
                        try:
                            representations.update(theory_representations(check_input_data(candidate)))
                        except Exception as exc:
                            error(f"{context}, collecting representations", exc)
                    check_stop()
                    try:
                        stored = store_lagrangian_theory(connection, candidate)
                    except Exception as exc:
                        group_counts.db_failed += 1
                        error(f"{context}, database insert", exc)
                    else:
                        if stored.inserted:
                            group_counts.added += 1
                            outcome = "added to DB"
                        else:
                            group_counts.existing += 1
                            outcome = "already in DB"
                        log(f"{context}: valid SCFT; {outcome} (theory {stored.theory_id}).")
            except BuildCancelled:
                raise
            except Exception as exc:
                error(f"{label}, enumeration", exc)
            finally:
                counts.include(group_counts)
                log(f"{label} summary: {group_counts.describe()}")

        check_stop()
        if build_cache:
            if not max_adams:
                log("Character cache skipped: full index order // 2 is zero.")
            else:
                log(f"Building character cache for {len(representations)} distinct representations "
                    f"through Adams order {max_adams}.")
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
                            timeout=settings["tools/timeout"], progress=progress,
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
    log(f"Build {status}. Total: {counts.describe()}; errors={errors}; "
        f"cached representations={cache_built}/{len(representations)}.")
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

    def log(message):
        if password:
            message = message.replace(password, "[redacted]")
        print(json.dumps({"log": message}, ensure_ascii=True), flush=True)

    result = run_build(request["text"], request["settings"], request["build_cache"], log, stopped.is_set)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
