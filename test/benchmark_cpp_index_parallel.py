"""Compare exact C++ expansion scaling at 1/2/4/8 requested workers.

Run with Sage's Python from the repository root:
  sage -python -B test/benchmark_cpp_index_parallel.py

Default inputs are simple, bifundamental and trifundamental cases at t18/t20.
Use --orders 18 20 22 to include a heavier cutoff. Only small JSON/Markdown
reports are retained; native binaries, FORM scripts and expansions are temporary.
"""

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from index import n2_theory_index as idx
from test import benchmark_cpp_index_expand as base


DEFAULT_CASES = ("su2_four_full_hypers", "product_bifundamental", "trifundamental")


def source_hashes():
    hashes = base.source_hashes()
    hashes["test/benchmark_cpp_index_parallel.py"] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    return hashes


def measure_cpp(directory, executable, native_input, workers, timeout):
    # Repeated exact comparisons retain large reference maps. Prevent unrelated
    # cyclic collections from landing in just one worker count's parsing sample.
    gc.collect()
    with tempfile.TemporaryDirectory(prefix=f"cpp-workers-{workers}-", dir=directory) as folder:
        result, record = base.run_process([executable, "--workers", workers],
                                          Path(folder), timeout, native_input)
        metadata = json.loads(result.stderr)
        record.update(metadata)
        gc_was_enabled = gc.isenabled()
        gc.disable()
        start = time.perf_counter()
        try:
            terms = base.parse_cpp_output(result.stdout)
        finally:
            parse_seconds = time.perf_counter() - start
            if gc_was_enabled:
                gc.enable()
        record["python_parse_seconds"] = parse_seconds
        record["process_plus_parse_seconds"] = record["process_seconds"] + record["python_parse_seconds"]
        if metadata["term_count"] != len(terms):
            raise AssertionError("native timing metadata has an incorrect term count")
        record["requested_workers"] = workers
        # Canonicalizing and comparing maps are outside every timed interval.
        return record, base.term_map(terms)


def execution_order(workers, repetition):
    offset = repetition % len(workers)
    ordered = workers[offset:] + workers[:offset]
    return [ordered[0], *reversed(ordered[1:])] if repetition % 2 else ordered


def report_markdown(report):
    lines = ["# C++ index expansion parallel scaling", "",
             f"Measured: {report['created_utc']}", "",
             f"Compiler: {report['build']['compiler_version']}. C++17, `-O3 -DNDEBUG -pthread`.", "",
             f"CPU: {report['machine']['cpu']}; {report['machine']['logical_cpus']} logical CPUs. "
             f"Reference: {report['machine']['form_version']}.", "",
             "Each input was first calculated by uncached, single-thread FORM. "
             "One C++ warmup per worker count and "
             f"{report['repetitions']} measured repetitions per worker count followed, "
             "rotating/reversing their execution order. Every C++ output was compared as a complete "
             "exact monomial-to-rational map with both FORM and the one-worker C++ warmup. "
             "Reported times are medians; all speedups use the measured one-worker C++ baseline.", "",
             "| Input | Cutoff | Terms | Workers | Native function (s) | Native speedup | Process + Python parse (s) | End-to-end speedup | Peak native RSS (MiB) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    parallel_ratios = []
    for case in report["cases"]:
        for workers in report["workers"]:
            row = case["summary"][str(workers)]
            if workers != 1:
                parallel_ratios.append((row["native_speedup_vs_one"], case["name"],
                                        case["order"], workers))
            lines.append(f"| {case['name']} | {case['order']} | {case['term_count']:,} | "
                         f"{workers} | {row['expansion_seconds']['median']:.5f} | "
                         f"{row['native_speedup_vs_one']:.2f}x | "
                         f"{row['process_plus_parse_seconds']['median']:.5f} | "
                         f"{row['end_to_end_speedup_vs_one']:.2f}x | "
                         f"{row['peak_rss_kib']['median'] / 1024:.2f} |")
    if parallel_ratios:
        best, worst = max(parallel_ratios), min(parallel_ratios)
        slowdowns = sum(ratio < 1 for ratio, *_ in parallel_ratios)
        lines += ["", "A speedup above 1 means faster than one-worker C++; below 1 means slower. "
                  f"Of {len(parallel_ratios)} multiworker comparisons, {slowdowns} were slower. "
                  f"The largest measured native speedup was {best[0]:.2f}x "
                  f"({best[1]}, t{best[2]}, {best[3]} workers); the lowest was {worst[0]:.2f}x "
                  f"({worst[1]}, t{worst[2]}, {worst[3]} workers)."]
    lines += ["", "Native timing surrounds only `expand_index`, including native input validation, "
              "worker management, arithmetic, combination of partial results, and final native "
              "term-vector construction/sorting. It excludes process launch, stdin parsing and text "
              "serialization. Requested worker counts are shown; implementation thresholds may "
              "keep small stages serial. Thread scheduling and merging overhead can outweigh "
              "parallel arithmetic for these input sizes.", "",
              "Process + Python parse includes GNU time/process launch, native computation, complete "
              "text output/transfer, exit, and construction of the same Python `IndexFormTerm` objects. "
              "Exact map comparisons are excluded. This test-only subprocess protocol is not a Python "
              "binding and does not measure the full application or character decomposition.", "",
              "To prevent unrelated collections of retained reference data from biasing individual "
              "worker counts, Python cyclic garbage is collected before each C++ measurement outside "
              "the timers, and cyclic garbage collection is disabled only during timed parsing and "
              "then restored. Reference counting remains enabled; this parser constructs acyclic "
              "term data. These controlled parsing timings are not predictions of production Python "
              "garbage-collection overhead.", "",
              "Peak RSS is GNU time's native child high-water resident memory, excluding the Sage/Python "
              "parent and Python parsing. Parallel partial polynomials and allocator behavior may "
              "increase memory. There is no persistent expansion cache or database access.", "",
              "The FORM reference below is a single correctness run for each input, not a repeated "
              "FORM timing benchmark or a TFORM comparison.", "",
              "| Input | Cutoff | FORM process (s), single reference run | FORM peak native RSS (MiB) |",
              "|---|---:|---:|---:|"]
    for case in report["cases"]:
        reference = case["form_reference"]
        lines.append(f"| {case['name']} | {case['order']} | {reference['process_seconds']:.5f} | "
                     f"{reference['peak_rss_kib'] / 1024:.2f} |")
    lines += ["", "Reproduce from the repository root:", "", "```sh",
              "PATH=/home/subo-lee/miniconda3/envs/sage/bin:$PATH \\",
              "  sage -python -B test/benchmark_cpp_index_parallel.py \\",
              "  --orders " + " ".join(map(str, report["orders"])) + " \\",
              "  --workers " + " ".join(map(str, report["workers"])) + " \\",
              "  --cases " + " ".join(report["selected_cases"]) + " \\",
              f"  --repetitions {report['repetitions']}", "```", "",
              "Compiler/GMP overrides are shared with `test/benchmark_cpp_index_expand.py` "
              "and documented in `test/cpp/README.md`. See `results.json` for raw repetitions, "
              "execution order, input data, compiler command, source hashes and machine details. "
              "These measurements do not establish scaling for other character bases, machines "
              "or substantially higher cutoffs.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "output/benchmarks/cpp_index_parallel_20260929")
    parser.add_argument("--orders", type=int, nargs="+", default=[18, 20])
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--cases", nargs="+", choices=tuple(base.benchmark_cases()),
                        default=list(DEFAULT_CASES))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.repetitions < 1 or args.timeout <= 0 or any(order < 2 for order in args.orders):
        parser.error("repetitions/timeout must be positive and cutoffs at least two")
    if 1 not in args.workers or any(worker < 1 for worker in args.workers):
        parser.error("worker counts must be positive and must include one")
    if len(args.workers) != len(set(args.workers)):
        parser.error("worker counts must be unique")
    form = shutil.which(os.environ.get("FORM_EXECUTABLE", "form"))
    if form is None or not Path("/usr/bin/time").is_file():
        parser.error("FORM and GNU /usr/bin/time are required")
    args.output.mkdir(parents=True, exist_ok=True)
    initial_hashes = source_hashes()
    cpu_info = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
    cpu_name = next((line.split(":", 1)[1].strip() for line in cpu_info.splitlines()
                     if line.startswith("model name")), platform.processor())
    with tempfile.TemporaryDirectory(prefix="benchmark-cpp-parallel-") as folder:
        directory = Path(folder)
        executable, build = base.compile_driver(directory)
        report = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
                  "repetitions": args.repetitions, "warmups_per_worker_count": 1,
                  "orders": args.orders, "workers": args.workers, "selected_cases": args.cases,
                  "python_gc_control": "collect before C++ process outside timers; disable cyclic GC only during timed parsing, restoring previous state",
                  "timeout_seconds": args.timeout, "build": build,
                  "source_sha256": initial_hashes, "source_stable_verified": False,
                  "machine": {"platform": platform.platform(), "cpu": cpu_name,
                              "logical_cpus": os.cpu_count(), "python": sys.version,
                              "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                              "load_average_before": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
                              "form_executable": form,
                              "form_version": subprocess.run([form, "-v"], check=True,
                                  capture_output=True, text=True, timeout=10).stdout.strip(),
                              "form_sha256": hashlib.sha256(Path(form).read_bytes()).hexdigest()},
                  "cases": []}
        for name in args.cases:
            character_count, vectors, matter = base.benchmark_cases()[name]
            for order in args.orders:
                program = idx._build_form_program(order, character_count, tuple(vectors), matter)
                native_input = base.cpp_input(order, character_count, vectors, matter)
                reference, expected = base.measure("form", directory, executable, program,
                                                   native_input, form, args.timeout)
                baseline = None
                warmups = {}
                samples = {str(worker): [] for worker in args.workers}
                warmup_order = [1, *(worker for worker in args.workers if worker != 1)]
                for worker in warmup_order:
                    record, actual = measure_cpp(directory, executable, native_input, worker, args.timeout)
                    if actual != expected:
                        raise AssertionError(f"FORM mismatch: {name}, t{order}, workers={worker}")
                    if worker == 1:
                        baseline = actual
                    elif actual != baseline:
                        raise AssertionError(f"one-worker mismatch: {name}, t{order}, workers={worker}")
                    warmups[str(worker)] = record
                orders = []
                for repetition in range(args.repetitions):
                    current_order = execution_order(args.workers, repetition)
                    orders.append(current_order)
                    for worker in current_order:
                        record, actual = measure_cpp(directory, executable, native_input, worker, args.timeout)
                        if actual != baseline or actual != expected:
                            raise AssertionError(f"exact mismatch: {name}, t{order}, workers={worker}, run={repetition}")
                        samples[str(worker)].append(record)
                summary = {worker: base.summarize(records) for worker, records in samples.items()}
                for worker, row in summary.items():
                    row["native_speedup_vs_one"] = summary["1"]["expansion_seconds"]["median"] / row["expansion_seconds"]["median"]
                    row["end_to_end_speedup_vs_one"] = summary["1"]["process_plus_parse_seconds"]["median"] / row["process_plus_parse_seconds"]["median"]
                case = {"name": name, "order": order, "character_count": character_count,
                        "vector_characters": list(vectors),
                        "matter_multiplicities": [[list(key), str(value)] for key, value in sorted(matter.items())],
                        "term_count": len(expected), "exact_equality": True,
                        "form_reference": reference, "warmups": warmups, "warmup_order": warmup_order,
                        "measured_execution_orders": orders, "samples": samples, "summary": summary}
                report["cases"].append(case)
                (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps({"case": name, "order": order, "terms": len(expected),
                                  "native_speedup": {worker: row["native_speedup_vs_one"]
                                                     for worker, row in summary.items()}}), flush=True)
        if source_hashes() != initial_hashes:
            raise RuntimeError("sources changed during benchmark; rerun before using measurements")
        report["source_stable_verified"] = True
        report["status"] = "complete"
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        report["machine"]["load_average_after"] = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
        (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output / "README.md").write_text(report_markdown(report))
        print(f"Report: {args.output / 'README.md'}", flush=True)


if __name__ == "__main__":
    main()
