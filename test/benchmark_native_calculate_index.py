"""Compare complete calculate_index backends with private caches and exact output.

Run with Sage. No application database or project cache is read or modified.
Reference workers force the original Python cache and executable LiE backend.
Native workers call the combined C++ expansion/cache/LiE/projection binding.
"""
import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fundamental(algebra, flavors):
    return {"algebra": algebra, "hypermultiplets": [{"representation": "fundamental", "number": flavors}]}


def cases():
    pair = {"gauge_groups": [{"id": x, "algebra": "A1"} for x in "ab"],
            "hypermultiplets": [{"representations": {x: "fundamental" for x in "ab"}, "number": 2}]}
    triple = {"gauge_groups": [{"id": x, "algebra": "A1"} for x in "abc"],
              "hypermultiplets": [{"representations": {x: "fundamental" for x in "abc"}, "number": 1}]}
    return [
        {"name": "SU2_Nf4", "data": fundamental("A1", 4), "order": 12, "workers": 1},
        {"name": "SU3_Nf6", "data": fundamental("A2", 6), "order": 12, "workers": 1},
        {"name": "Sp4_Nf6", "data": fundamental("C2", 6), "order": 12, "workers": 1},
        {"name": "SU2xSU2_bifundamental", "data": pair, "order": 12, "workers": 1},
        {"name": "SU2_cubed_trifundamental", "data": triple, "order": 26, "workers": 32},
        {"name": "SU33_Nf66", "data": fundamental("A32", 66), "order": 18, "workers": 32},
    ]


def worker(config):
    from index import n2_theory_index as idx
    case = config["case"]
    # Warm Python/Sage root-system and representation metadata equally. The
    # measured public API still performs its own validation on every call.
    idx._parse_input(case["data"])
    if config["backend"] == "native":
        idx._require_native_index()
    records, coefficients = [], None
    with tempfile.TemporaryDirectory(prefix="index-backend-bench-") as temporary:
        path = Path(temporary)
        for phase in ("cold", "warm_characters", "warm_all"):
            form_cache = path / ("fresh-form.db" if phase == "warm_characters" else "form.db")
            gc.collect()
            before = resource.getrusage(resource.RUSAGE_SELF)
            child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            start = time.perf_counter()
            result = idx.calculate_index(case["data"], case["order"],
                backend=config["backend"], worker_count=case["workers"], processes=case["workers"],
                timeout=config["timeout"], char_cache_database_path=path / "characters.db",
                form_cache_database_path=form_cache)
            seconds = time.perf_counter() - start
            after = resource.getrusage(resource.RUSAGE_SELF)
            child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
            exact = [[list(map(int, powers)), str(value.numerator()), str(value.denominator())]
                     for powers, value in sorted(result.dict().items())]
            if coefficients is not None and exact != coefficients:
                raise AssertionError("cache state changed exact coefficients")
            coefficients = exact
            records.append({"phase": phase, "seconds": seconds,
                "cpu_seconds": after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
                    + child_after.ru_utime + child_after.ru_stime - child_before.ru_utime - child_before.ru_stime,
                "self_peak_rss_kib": after.ru_maxrss, "largest_child_peak_rss_kib": child_after.ru_maxrss,
                "cache_bytes": {name: (path / name).stat().st_size for name in
                    ("characters.db", "form.db", "fresh-form.db") if (path / name).exists()}})
            print(json.dumps({"progress": phase, "backend": config["backend"],
                              "case": case["name"], "seconds": seconds}), flush=True)
    print(json.dumps({"records": records, "coefficients": coefficients}), flush=True)


def render(report):
    lines = ["# Native calculate_index versus original Python pipeline", "",
        f"Measured {report['created_utc']}; {report['repetitions']} fresh process repetitions per backend.", "",
        "Times are median wall seconds inside the public `calculate_index` call, including validation, "
        "expansion, cache operations, projection, Sage conversion and sector multiplication. "
        "Interpreter/import/build time is excluded; root-system metadata is initialized equally before timing.", "",
        "The original path uses FORM/TFORM plus Python projection, pure Python cache logic and executable LiE. "
        "The native path uses C++ expansion, SQLite cache and in-process LiE projection. "
        "All complete exact coefficient maps match across backends, repetitions and cache states.", "",
        "Workers=1 uses one expansion thread and one LiE worker. Workers=32 uses 32 C++ expansion workers "
        "or TFORM `-w32`; the Python reference also allows 32 LiE cache-generation processes. "
        "Native projection currently remains serial; these are requested worker limits, not promises of "
        "32-way utilization throughout the calculation.", "",
        "Cold means new empty caches. Warm characters retains character/singlet caches but gives FORM a "
        "fresh expansion-cache path. Warm all also reuses the original FORM expansion cache; native never "
        "writes an expansion cache. All files are private temporary files; no project cache or MySQL is used.", "",
        "| Theory | Cutoff | Workers | Cache state | Native (s) | Python (s) | Python/native |",
        "|---|---:|---:|---|---:|---:|---:|"]
    for case in report["cases"]:
        for phase in ("cold", "warm_characters", "warm_all"):
            medians = {backend: statistics.median(sample["seconds"] for run in case["runs"]
                if run["backend"] == backend for sample in run["records"] if sample["phase"] == phase)
                for backend in ("native", "python")}
            lines.append(f"| {case['name']} | {case['order']} | {case['workers']} | {phase} | "
                         f"{medians['native']:.4f} | {medians['python']:.4f} | "
                         f"{medians['python']/medians['native']:.2f}x |")
    lines += ["", "Ratios above 1 favor native; ratios below 1 favor Python. "
              "These measurements apply to these inputs and cache states. "
              "Peak RSS fields in the JSON are per-process high-water marks, not summed process-tree RAM.", "",
              "`results.json` retains exact coefficients, every sample, inputs, CPU/build metadata and source hashes."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker")
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", type=Path, default=ROOT / "output/benchmarks/native_calculate_index_20261015")
    args = parser.parse_args()
    if args.worker:
        worker(json.loads(args.worker))
        return
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    selected = [case for case in cases() if args.cases is None or case["name"] in args.cases]
    if not selected:
        parser.error("no cases selected")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "repetitions": args.repetitions,
              "timeout_seconds": args.timeout, "python": sys.executable,
              "machine": {"platform": platform.platform(), "cpus": os.cpu_count(),
                          "affinity": sorted(os.sched_getaffinity(0)),
                          "cpuinfo": next(line for line in Path("/proc/cpuinfo").read_text().splitlines()
                                          if line.startswith("model name"))},
              "build": json.loads((ROOT / "index/cpp/_build/last_build.json").read_text()),
              "sources": {}, "cases": []}
    for path in ("index/n2_theory_index.py", "index/cpp/index_expand.cpp",
                 "index/cpp/index_singlet_project.cpp", "index/cpp/char_decomposition_cache.cpp",
                 "index/cpp/char_decomposition_bindings.cpp", "common/cpp/lie.cpp",
                 "test/benchmark_native_calculate_index.py"):
        report["sources"][path] = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    for case in selected:
        record = {**case, "runs": []}
        report["cases"].append(record)
        exact = None
        for repeat in range(args.repetitions):
            for backend in (("native", "python") if repeat % 2 == 0 else ("python", "native")):
                print(f"{case['name']} workers={case['workers']} {backend} repetition={repeat+1}", flush=True)
                config = {"case": case, "backend": backend, "timeout": args.timeout}
                environment = dict(os.environ, N2SCFTDB_CHAR_CACHE_BACKEND="python", PYTHONDONTWRITEBYTECODE="1")
                process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                    "--worker", json.dumps(config)], cwd=ROOT, env=environment,
                    stdout=subprocess.PIPE, text=True)
                run = None
                for line in process.stdout:
                    item = json.loads(line)
                    if "records" in item:
                        run = item
                    else:
                        print(line.strip(), flush=True)
                if process.wait() or run is None:
                    raise RuntimeError("benchmark worker failed")
                if exact is not None and exact != run["coefficients"]:
                    raise AssertionError(f"exact backend mismatch for {case['name']}")
                exact = run["coefficients"]
                record["runs"].append({"backend": backend, "repetition": repeat + 1, **run})
                (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        (args.output / "README.md").write_text(render(report))
    print(args.output / "README.md", flush=True)


if __name__ == "__main__":
    main()
