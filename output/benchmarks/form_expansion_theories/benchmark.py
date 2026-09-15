"""Compare full indices with a prefilled character cache and three FORM modes.

Only benchmark-owned files are written. Run with Sage's Python. Existing project
caches seed copies through SQLite's backup API; source connections are read-only.
"""

import argparse
from contextlib import closing, ExitStack
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from index import n2_theory_index as idx
from index import form_expansion_cache as form_module
from index.char_decomposition_cache import CharacterDecompositionCache

BaseFormCache = form_module.FormExpansionCache
ACTIVE = None
MODES = ("disabled", "miss", "hit")


def simple(algebra, *matter):
    return {"algebra": algebra, "hypermultiplets": [
        {"representation": rep, "number": number, "kind": "full"}
        for rep, number in matter
    ]}


CASES = [
    ("su2_4fund", "SU(2), 4 fundamental hypers", simple("A1", ("fundamental", 4))),
    ("su3_6fund", "SU(3), 6 fundamental hypers", simple("A2", ("fundamental", 6))),
    ("su5_10fund", "SU(5), 10 fundamental hypers", simple("A4", ("fundamental", 10))),
    ("su3_adjoint", "SU(3), 1 adjoint hyper (N=4)", simple("A2", ("adjoint", 1))),
    ("sp2_6fund", "Sp(2) = USp(4), 6 fundamental hypers", simple("C2", ("fundamental", 6))),
    ("spin8_6vec", "Spin(8), 6 vector hypers", simple("D4", ("vector", 6))),
    ("g2_4fund", "G2, 4 fundamental hypers", simple("G2", ("fundamental", 4))),
    ("su2_su2_bifund", "SU(2) x SU(2), 2 bifundamental hypers", {
        "gauge_groups": [{"id": "a", "algebra": "A1"}, {"id": "b", "algebra": "A1"}],
        "hypermultiplets": [{"representations": {"a": "fundamental", "b": "fundamental"},
                            "number": 2, "kind": "full"}],
    }),
    ("su5_sym_asym", "SU(5), 1 symmetric + 1 antisymmetric hyper",
     simple("A4", ("symmetric", 1), ("antisymmetric", 1))),
]


def measured(name, operation):
    @wraps(operation)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return operation(*args, **kwargs)
        finally:
            ACTIVE["stages_seconds"][name] = ACTIVE["stages_seconds"].get(name, 0) + time.perf_counter() - start
            ACTIVE["calls"][name] = ACTIVE["calls"].get(name, 0) + 1
    return wrapper


class TimedFormCache(BaseFormCache):
    parse_form_output = staticmethod(measured("parse_form", BaseFormCache.parse_form_output))
    _encode_expansion = staticmethod(measured("serialize", BaseFormCache._encode_expansion))
    _decode_expansion = staticmethod(measured("deserialize", BaseFormCache._decode_expansion))

    def get_expansion(self, program):
        start = time.perf_counter()
        if ACTIVE["mode"] == "disabled":
            output = form_module.run_form(program, form_executable=self.form_executable, timeout=self.timeout)
            terms = self.parse_form_output(output)
        else:
            terms = super().get_expansion(program)
        ACTIVE["stages_seconds"]["form_stage"] = time.perf_counter() - start
        ACTIVE["expansion_terms"] = len(terms)
        return terms


class WarmOnlyCharacterCache(CharacterDecompositionCache):
    """Fail if any timed run needs a new character/singlet computation or write."""

    def _connection(self):
        connection = super()._connection()
        state = self._state()
        if not state.get("benchmark_query_only"):
            connection.execute("PRAGMA query_only=ON")
            state["benchmark_query_only"] = True
        return connection

    def _execute_lie(self, *args, **kwargs):
        raise AssertionError("LiE was requested during a prefilled-cache timing")

    def _calculate_decomposition(self, *args, **kwargs):
        raise AssertionError("a new character decomposition was requested during timing")

    def _compute_singlet_multiplicities(self, *args, **kwargs):
        raise AssertionError("a new singlet coefficient was requested during timing")


def seed_database(source, target):
    if target.exists() or not source.exists():
        return
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dest:
            src.backup(dest)


def character_counts(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return {name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in ("character_decompositions", "singlet_coefficients")}


def summarize(values):
    ordered = sorted(values)
    return {"median": statistics.median(values), "min": ordered[0], "max": ordered[-1]}


def write_report(destination, report):
    (destination / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        "# Full-index benchmark with a prefilled character cache", "",
        "All matter entries are full hypermultiplets. Every theory passes the current input/anomaly and vanishing-beta checks.", "",
        f"{report['repeats']} measured repetitions per mode, after {report['warmups']} warmup cycle(s); medians in seconds. Mode order rotates between rounds. Each calculation opens new cache clients.", "",
        "| Theory | Order | FORM terms | Cache disabled | Cache miss + save | Cache hit | Disabled / hit |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        modes = case["summary"]
        lines.append(f"| {case['label']} | {case['order']} | {case['expansion_terms']:,} | "
                     f"{modes['disabled']['total_seconds']['median']:.4f} | "
                     f"{modes['miss']['total_seconds']['median']:.4f} | "
                     f"{modes['hit']['total_seconds']['median']:.4f} | {case['speedup_disabled_over_hit']:.2f}x |")
    lines += ["", "## Method", ""] + ["- " + note for note in report["notes"]]
    lines += ["", "## Detailed stages", "",
              "Stage medians are measured independently and may not sum exactly to the total. FORM-stage time includes its cache lookup/write as applicable; the full total also includes input validation, program construction, cache closure, projection and Sage polynomial construction.", "",
              "| Theory | Order | Mode | Full total | FORM execute | FORM parse | JSON encode | JSON decode | FORM stage total | Projection |",
              "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for case in report["cases"]:
        for mode in MODES:
            summary = case["summary"][mode]
            stages = summary["stages_seconds"]
            values = [summary["total_seconds"]["median"]] + [
                stages.get(name, {"median": 0})["median"] for name in
                ("run_form", "parse_form", "serialize", "deserialize", "form_stage", "projection")]
            lines.append(f"| {case['label']} | {case['order']} | {mode} | " +
                         " | ".join(f"{value:.4f}" for value in values) + " |")
    lines += ["", "## Reproduce", "", "From the project root:", "", "```bash",
              "DOT_SAGE=/tmp/codex-sage-cache PYTHONDONTWRITEBYTECODE=1 /home/subo-lee/miniconda3/envs/sage/bin/sage -python -B output/benchmarks/form_expansion_theories/benchmark.py",
              "```", "", "The benchmark keeps its prefilled databases under `cache/`; the project-root caches are only read to seed copies. Exact inputs, FORM programs, final index coefficients, cache row counts, prefilling durations, individual trials and environment details are in [results.json](results.json).", ""]
    (destination / "report.md").write_text("\n".join(lines))


def main():
    global ACTIVE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--orders", nargs="+", type=int, default=[12, 18])
    parser.add_argument("--cases", nargs="+", choices=[case[0] for case in CASES])
    parser.add_argument("--output-directory", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    if args.repeats < 1 or args.warmups < 0 or any(order < 2 for order in args.orders):
        parser.error("positive repeats, nonnegative warmups and orders >= 2 required")
    destination = args.output_directory.resolve()
    cache_dir = destination / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    char_db = cache_dir / "character.db"
    hit_db = cache_dir / "form_hit.db"
    miss_db = cache_dir / "form_miss.db"
    seed_database(ROOT / "char_decomposition_cache.db", char_db)
    seed_database(ROOT / "form_expansion_cache.db", hit_db)
    with BaseFormCache(database_path=miss_db) as cache:
        cache._connection()
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
        "repeats": args.repeats, "warmups": args.warmups, "cases": [],
        "character_database": str(char_db), "hit_database": str(hit_db),
        "notes": [
            "Full calculate_index wall time; Python/Sage startup, benchmark setup, prefilling and equality checks are excluded.",
            "Before timing each case/order, a full calculation fills the character decomposition and final singlet caches. These rows remain unchanged during its timings.",
            "During timing the character DB is query-only, LiE and direct decomposition entry points raise errors, and any missing singlet would require a forbidden write. Each successful timing therefore uses a fully prefilled character cache.",
            "Disabled mode bypasses FORM SQLite entirely and runs the same FORM executor/parser; miss and hit use the current get_expansion implementation.",
            "Miss mode starts with an initialized empty FORM database for each trial; its time includes JSON serialization and durable insertion. Clearing that database is outside the timer.",
            "Hit mode reuses a populated FORM SQLite database and sets an unavailable FORM executable as a safeguard. Every hit executes and parses FORM zero times.",
            "All modes use processes=1, new cache clients per calculation, normal garbage collection, warm OS file caches, and no competing benchmark workers.",
            "Every result is checked for exact Laurent-polynomial equality to the prefill result outside the timer. Prefilled-cache row counts are also checked before and after each case/order.",
            "Timing and mode-selection instrumentation is confined to this benchmark script; no application source is changed.",
            "Both benchmark databases reside on the project filesystem; SQLite uses the application's WAL and default synchronous settings.",
        ],
    }
    for case_id, label, data in CASES:
        if args.cases and case_id not in args.cases:
            continue
        checked = idx.check_input_data(data)
        assert not checked["errors"], checked
        assert checked["lagrangian_scft_candidate"], checked
        for order in args.orders:
            print(json.dumps({"case": case_id, "order": order, "stage": "prefill"}), flush=True)
            kwargs = dict(char_cache_database_path=char_db, processes=1, timeout=600)
            start = time.perf_counter()
            reference = idx.calculate_index(data, order, form_cache_database_path=hit_db, **kwargs)
            prefill_seconds = time.perf_counter() - start
            counts_before = character_counts(char_db)
            factors, hypers = idx._parse_input(data)
            specs, vectors, matter = idx._character_basis(factors, hypers)
            program = idx._build_form_program(order, len(specs), vectors, matter)
            with closing(sqlite3.connect(hit_db)) as connection:
                payload_bytes = connection.execute(
                    "SELECT length(expansion_json) FROM form_expansions WHERE program=?", (program,)
                ).fetchone()[0]
            records = []
            with ExitStack() as stack:
                stack.enter_context(patch.object(idx, "CharacterDecompositionCache", WarmOnlyCharacterCache))
                stack.enter_context(patch.object(idx, "FormExpansionCache", TimedFormCache))
                stack.enter_context(patch.object(form_module, "run_form", measured("run_form", form_module.run_form)))
                stack.enter_context(patch.object(idx, "_project_terms", measured("projection", idx._project_terms)))
                for round_number in range(args.warmups + args.repeats):
                    # Rotate the three possible positions for each mode.
                    offset = round_number % len(MODES)
                    for mode in MODES[offset:] + MODES[:offset]:
                        if mode == "miss":
                            with closing(sqlite3.connect(miss_db)) as connection:
                                with connection:
                                    connection.execute("DELETE FROM form_expansions")
                        ACTIVE = {"mode": mode, "round": round_number - args.warmups,
                                  "stages_seconds": {}, "calls": {}}
                        start = time.perf_counter()
                        result = idx.calculate_index(
                            data, order, **kwargs,
                            lie_executable="LiE-must-not-run-during-timing",
                            form_cache_database_path=hit_db if mode == "hit" else miss_db,
                            form_executable="FORM-must-not-run-on-hit" if mode == "hit" else "form",
                        )
                        ACTIVE["total_seconds"] = time.perf_counter() - start
                        assert result == reference, (case_id, order, mode)
                        assert ACTIVE["calls"].get("run_form", 0) == (0 if mode == "hit" else 1)
                        assert ACTIVE["calls"].get("parse_form", 0) == (0 if mode == "hit" else 1)
                        assert ACTIVE["calls"].get("deserialize", 0) == (1 if mode == "hit" else 0)
                        assert ACTIVE["calls"].get("serialize", 0) == (1 if mode == "miss" else 0)
                        if round_number >= args.warmups:
                            records.append(ACTIVE)
                        del result
            counts_after = character_counts(char_db)
            assert counts_before == counts_after
            summary = {}
            for mode in MODES:
                values = [record for record in records if record["mode"] == mode]
                stage_names = {name for record in values for name in record["stages_seconds"]}
                summary[mode] = {
                    "total_seconds": summarize([record["total_seconds"] for record in values]),
                    "stages_seconds": {name: summarize([record["stages_seconds"].get(name, 0) for record in values])
                                       for name in sorted(stage_names)},
                }
            entry = {
                "case": case_id, "label": label, "input": data, "order": order,
                "program": program, "formal_characters": len(specs), "json_bytes": payload_bytes,
                "expansion_terms": records[0]["expansion_terms"], "prefill_seconds": prefill_seconds,
                "character_rows_before": counts_before, "character_rows_after": counts_after,
                "coefficients": [[list(map(int, powers)), str(value)] for powers, value in sorted(reference.dict().items())],
                "all_results_equal": True, "records": records, "summary": summary,
                "speedup_disabled_over_hit": summary["disabled"]["total_seconds"]["median"] / summary["hit"]["total_seconds"]["median"],
            }
            report["cases"].append(entry)
            report["updated_utc"] = datetime.now(timezone.utc).isoformat()
            write_report(destination, report)
            print(json.dumps({"case": case_id, "order": order, "stage": "complete",
                              "prefill_seconds": prefill_seconds,
                              "median_seconds": {mode: summary[mode]["total_seconds"]["median"] for mode in MODES},
                              "speedup": entry["speedup_disabled_over_hit"]}), flush=True)
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    write_report(destination, report)


if __name__ == "__main__":
    main()
