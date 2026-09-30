"""Benchmark exact uncached C++/FORM expansions; no production integration.

Run with Sage's Python, for example:
  sage -python -B test/benchmark_cpp_index_expand.py --repetitions 3

Only small JSON/Markdown reports are retained. Native executable, FORM programs,
outputs and scratch files are temporary. See test/cpp/README.md for GMP setup.
"""

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from index import n2_theory_index as idx
from index.form_expansion_cache import FormExpansionCache, IndexFormTerm


def source_hashes():
    names = ("common/cpp/index_expand.hpp", "common/cpp/index_expand.cpp",
             "test/cpp/index_expand_driver.cpp", "test/benchmark_cpp_index_expand.py",
             "index/n2_theory_index.py", "index/form_expansion_cache.py")
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def compile_driver(directory):
    compiler = shlex.split(os.environ.get("CXX", "/usr/bin/g++"))
    include = os.environ.get("GMP_INCLUDE_DIR")
    library = os.environ.get("GMP_LIBRARY_DIR")
    if include is None and (Path(sys.prefix) / "include/gmpxx.h").is_file():
        include = str(Path(sys.prefix) / "include")
        if library is None:
            library = str(Path(sys.prefix) / "lib")
    executable = directory / "index_expand_driver"
    command = [*compiler, "-std=c++17", "-O3", "-DNDEBUG", "-pthread", "-Wall", "-Wextra", "-Wpedantic"]
    command += shlex.split(os.environ.get("CPP_INDEX_CXXFLAGS", ""))
    command += ["-I", str(ROOT)]
    if include:
        command += ["-I", include]
    command += [str(ROOT / "test/cpp/index_expand_driver.cpp"),
                str(ROOT / "common/cpp/index_expand.cpp")]
    if library:
        command += ["-L", library, f"-Wl,-rpath,{library}"]
    command += ["-lgmpxx", "-lgmp", "-o", str(executable)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    version = subprocess.run([*compiler, "--version"], check=True,
                             capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
    return executable, {"command": command, "compiler_version": version,
                        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}


def benchmark_cases():
    product_data = json.loads((ROOT / "anomalies/example_product_bifundamental.json").read_text())
    factors, hypers = idx._parse_input(product_data)
    specs, vectors, matter = idx._character_basis(factors, hypers)
    return {
        "free_matter": (0, (), {(): 2}),
        "su2_four_full_hypers": (2, (0,), {(1,): 8}),
        "product_bifundamental": (len(specs), vectors, matter),
        "trifundamental": (6, (0, 1, 2), {(3, 4, 5): 1}),
    }


def cpp_input(order, character_count, vectors, matter):
    lines = [f"{order} {character_count} {len(vectors)}",
             " ".join(map(str, vectors)), str(len(matter))]
    lines.extend(" ".join(map(str, (multiplicity, len(monomial), *monomial)))
                 for monomial, multiplicity in sorted(matter.items()))
    return "\n".join(lines) + "\n"


def parse_cpp_output(output):
    terms = []
    for line in output.splitlines():
        fields = list(map(int, line.split()))
        t, y, u, numerator, denominator, count = fields[:6]
        position = 6
        characters = []
        for _ in range(count):
            character, length = fields[position:position + 2]
            position += 2
            characters.append((character, tuple(fields[position:position + length])))
            position += length
        if position != len(fields) or denominator <= 0:
            raise ValueError("invalid native test driver output")
        terms.append(IndexFormTerm(Fraction(numerator, denominator), t, y, u, tuple(characters)))
    return terms


def term_map(terms):
    result = {(term.t_power, term.y_power, term.u_power, term.characters): term.coefficient
              for term in terms}
    if len(result) != len(terms):
        raise ValueError("duplicate formal monomials")
    return result


def run_process(command, directory, timeout, input_text=None):
    # GNU time obtains kernel high-water RSS rather than sampling fast processes.
    # Its identical launcher cost is included for both engines' process times.
    usage_path = directory / "resource.json"
    timed_command = ["/usr/bin/time", "-f",
                     '{"peak_rss_kib":%M,"user_seconds":%U,"system_seconds":%S}',
                     "-o", str(usage_path), *map(str, command)]
    start = time.perf_counter()
    process = subprocess.Popen(timed_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, cwd=directory,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Terminate GNU time and its computation child together.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    result = subprocess.CompletedProcess(timed_command, process.returncode, stdout, stderr)
    seconds = time.perf_counter() - start
    if result.returncode:
        raise RuntimeError(f"process failed ({result.returncode}): {result.stderr or result.stdout}")
    usage = json.loads(usage_path.read_text())
    return result, {"process_seconds": seconds, "stdout_bytes": len(result.stdout.encode()), **usage}


def measure(engine, directory, executable, program, native_input, form, timeout):
    # Input preparation and file creation are excluded for both engines.
    with tempfile.TemporaryDirectory(prefix=f"{engine}-", dir=directory) as folder:
        scratch = Path(folder)
        if engine == "form":
            source = scratch / "program.frm"
            source.write_text(program)
            result, record = run_process([form, "-q", source], scratch, timeout)
            if result.stderr.strip():
                raise RuntimeError(result.stderr)
            parser = FormExpansionCache.parse_form_output
        else:
            result, record = run_process([executable], scratch, timeout, native_input)
            record.update(json.loads(result.stderr))
            parser = parse_cpp_output
        start = time.perf_counter()
        terms = parser(result.stdout)
        record["python_parse_seconds"] = time.perf_counter() - start
        record["process_plus_parse_seconds"] = record["process_seconds"] + record["python_parse_seconds"]
        record["term_count"] = len(terms)
        # Comparing maps is deliberately outside every timed interval.
        return record, term_map(terms)


def summarize(samples):
    keys = ("process_seconds", "python_parse_seconds", "process_plus_parse_seconds",
            "peak_rss_kib", "user_seconds", "system_seconds", "stdout_bytes", "expansion_seconds")
    return {key: {"median": statistics.median(row[key] for row in samples),
                  "min": min(row[key] for row in samples), "max": max(row[key] for row in samples)}
            for key in keys if key in samples[0]}


def report_markdown(report):
    lines = ["# Uncached FORM and C++ expansion benchmark", "",
             f"Measured: {report['created_utc']}", "",
             f"Compiler: {report['build']['compiler_version']}. C++17, `-O3 -DNDEBUG`.", "",
             f"FORM: {report['machine']['form_version']}", "",
             "One warmup and full exact comparison per case, followed by "
             f"{report['repetitions']} measured runs per engine, alternating execution order. "
             "Reported times are medians. Both engines use one computation thread. "
             "No FORM expansion cache, character decomposition, SQLite or database access.", "",
             "| Input | Cutoff | Terms | FORM process (s) | C++ process (s) | Process speedup | C++ function (s) | FORM + Python parse (s) | C++ + Python parse (s) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["cases"]:
        form = row["summary"]["form"]
        cpp = row["summary"]["cpp"]
        lines.append(f"| {row['name']} | {row['order']} | {row['term_count']:,} | "
                     f"{form['process_seconds']['median']:.4f} | {cpp['process_seconds']['median']:.4f} | "
                     f"{row['process_speedup']:.2f}x | {cpp['expansion_seconds']['median']:.4f} | "
                     f"{form['process_plus_parse_seconds']['median']:.4f} | "
                     f"{cpp['process_plus_parse_seconds']['median']:.4f} |")
    lines += ["", "A speedup above 1 means the C++ process was faster.", "",
              "The process comparison includes launch, computation, output serialization, "
              "pipe transfer and process exit for both programs; input construction is excluded. "
              "Both are wrapped by the same GNU time launcher. FORM prints its normal symbolic "
              "expression; the C++ test driver prints a compact numeric term format. This compares "
              "usable outputs, so output-format costs differ. C++ function timing includes construction "
              "of its final native term vector, but excludes launch, input parsing and text output; "
              "it must not be interpreted as a direct FORM-kernel timing comparison.", "",
              "The last two columns add materialization into the same Python `IndexFormTerm` type. "
              "They describe this test harness only: there is no Python binding or production "
              "backend integration. All measured outputs were compared coefficient-for-coefficient "
              "with the warmup reference outside the timed intervals.", "",
              "Peak resident memory is GNU time's per-child high-water mark in KiB, excluding "
              "the Sage/Python parent and Python parsing. FORM's reserved buffers can affect this "
              "measurement; there is no claimed total application-memory reduction.", "",
              "| Input | Cutoff | FORM peak RSS (KiB) | C++ peak RSS (KiB) |",
              "|---|---:|---:|---:|"]
    for row in report["cases"]:
        lines.append(f"| {row['name']} | {row['order']} | "
                     f"{row['summary']['form']['peak_rss_kib']['median']:,.0f} | "
                     f"{row['summary']['cpp']['peak_rss_kib']['median']:,.0f} |")
    lines += ["", "These cases cover small free, simple, two-factor and three-factor formal inputs. "
              "Their results do not establish performance on larger character bases or higher cutoffs, "
              "nor compare against parallel TFORM or cached FORM. See `results.json` for raw repetitions, "
              "machine details, exact inputs and source hashes.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "output/benchmarks/cpp_index_expand_20260929")
    parser.add_argument("--orders", type=int, nargs="+", default=[12, 18])
    parser.add_argument("--cases", nargs="+", choices=tuple(benchmark_cases()),
                        default=list(benchmark_cases()))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.repetitions < 1 or args.timeout <= 0 or any(order < 2 for order in args.orders):
        parser.error("repetitions and timeout must be positive; orders must be at least two")
    form = shutil.which(os.environ.get("FORM_EXECUTABLE", "form"))
    if form is None or not Path("/usr/bin/time").is_file():
        parser.error("FORM and GNU /usr/bin/time are required")
    args.output.mkdir(parents=True, exist_ok=True)
    initial_hashes = source_hashes()
    cpu_info = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
    cpu_name = next((line.split(":", 1)[1].strip() for line in cpu_info.splitlines()
                     if line.startswith("model name")), platform.processor())
    with tempfile.TemporaryDirectory(prefix="benchmark-cpp-index-") as folder:
        directory = Path(folder)
        executable, build = compile_driver(directory)
        report = {"created_utc": datetime.now(timezone.utc).isoformat(),
                  "repetitions": args.repetitions, "warmups": 1, "timeout_seconds": args.timeout,
                  "build": build, "source_sha256": initial_hashes,
                  "machine": {"platform": platform.platform(), "cpu": cpu_name,
                              "logical_cpus": os.cpu_count(), "python": sys.version,
                              "form_executable": form,
                              "form_version": subprocess.run([form, "-v"], check=True,
                                  capture_output=True, text=True, timeout=10).stdout.strip(),
                              "form_sha256": hashlib.sha256(Path(form).read_bytes()).hexdigest()},
                  "cases": []}
        for name in args.cases:
            character_count, vectors, matter = benchmark_cases()[name]
            for order in args.orders:
                program = idx._build_form_program(order, character_count, tuple(vectors), matter)
                native_input = cpp_input(order, character_count, vectors, matter)
                expected = None
                samples = {"form": [], "cpp": []}
                for repetition in range(-1, args.repetitions):
                    engines = ("form", "cpp") if repetition % 2 else ("cpp", "form")
                    for engine in engines:
                        record, actual = measure(engine, directory, executable, program,
                                                 native_input, form, args.timeout)
                        if expected is None:
                            expected = actual
                        elif actual != expected:
                            raise AssertionError(f"exact expansion mismatch: {name}, t{order}, {engine}")
                        if repetition >= 0:
                            samples[engine].append(record)
                summary = {engine: summarize(records) for engine, records in samples.items()}
                row = {"name": name, "order": order, "character_count": character_count,
                       "vector_characters": list(vectors),
                       "matter_multiplicities": [[list(key), str(value)] for key, value in sorted(matter.items())],
                       "exact_equality": True, "term_count": len(expected), "samples": samples,
                       "summary": summary,
                       "process_speedup": summary["form"]["process_seconds"]["median"] /
                                          summary["cpp"]["process_seconds"]["median"]}
                report["cases"].append(row)
                print(json.dumps({key: row[key] for key in ("name", "order", "term_count", "process_speedup")}), flush=True)
                (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        if source_hashes() != initial_hashes:
            raise RuntimeError("sources changed during benchmark; rerun before using these measurements")
        (args.output / "README.md").write_text(report_markdown(report))
        print(f"Report: {args.output / 'README.md'}", flush=True)


if __name__ == "__main__":
    main()
