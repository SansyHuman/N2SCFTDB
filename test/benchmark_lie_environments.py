"""Measure uncached LiE calls through one native cache per worker thread.

Each worker-count/workload runs in a fresh process. Initialization is reported
separately; all results are compared exactly with a serial reference. No cache
database is opened. Run with the interpreter used to build the native module.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from index import _index_calculation_cpp as native


def rss_kib():
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                  if ':' in line)
    return int(fields['VmRSS'].split()[0])


def run_case(case, workers, calls, repetitions):
    algebra = 'A8' if case == 'su9' else 'E8'
    labels = (1, 0, 0, 0, 0, 0, 0, 1) if case == 'su9' else (0, 0, 0, 0, 0, 0, 0, 1)
    caches = []
    with tempfile.TemporaryDirectory(prefix='n2-lie-environments-') as temporary:
        reference = native.CharacterDecompositionCache(str(Path(temporary) / 'unused.db'))
        try:
            psi = {p: reference.calculate_decomposition(algebra, labels, (0,) * (p - 1) + (1,))
                   for p in (2, 3)}
            operands = [(psi[2], psi[3]), (psi[3], psi[3])]
            expected = [reference.tensor_decompositions(algebra, *pair) for pair in operands]
        finally:
            reference.close()
        baseline_rss = rss_kib()
        local = threading.local()
        ready = threading.Barrier(workers + 1, timeout=60)

        def initialize():
            local.cache = native.CharacterDecompositionCache(str(Path(temporary) / 'unused.db'))
            caches.append(local.cache)
            local.cache.calculate_decomposition('A1', (1,), (0, 1))
            ready.wait()

        def calculate(chunk):
            return [(i, local.cache.tensor_decompositions(algebra, *operands[i % len(operands)]))
                    for i in range(chunk, calls, workers)]

        wall, cpu = [], []
        try:
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(initialize) for _ in range(workers)]
                ready.wait()
                for future in futures:
                    future.result()
                setup_seconds = time.perf_counter() - start
                idle_rss = rss_kib()
                for _ in range(repetitions):
                    begin, cpu_begin = time.perf_counter(), time.process_time()
                    results = list(pool.map(calculate, range(workers)))
                    wall.append(time.perf_counter() - begin)
                    cpu.append(time.process_time() - cpu_begin)
                    for chunk in results:
                        for i, result in chunk:
                            if result != expected[i % len(expected)]:
                                raise AssertionError(f'nonmatching decomposition at operation {i}')
            if list(Path(temporary).iterdir()):
                raise AssertionError('benchmark unexpectedly opened a database')
            return dict(case=case, workers=workers, calls=calls,
                        setup_seconds=setup_seconds, baseline_rss_kib=baseline_rss,
                        initialized_rss_kib=idle_rss, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        wall_seconds=wall, cpu_seconds=cpu, median_seconds=statistics.median(wall),
                        result_terms=[len(result) for result in expected],
                        result_sha256=[hashlib.sha256(native.encode_decomposition(result).encode()).hexdigest()
                                       for result in expected])
        finally:
            for cache in caches:
                cache.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 2, 4, 8, 16, 32])
    parser.add_argument('--calls', type=int, default=256)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/benchmarks/lie_environments_20261020')
    parser.add_argument('--run-case', choices=['su9', 'e8'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if min(args.workers) < 1 or args.calls < max(args.workers) or args.repetitions < 1:
        parser.error('positive workers/repetitions and at least one call per worker are required')
    if args.run_case:
        print(json.dumps(run_case(args.run_case, args.workers[0], args.calls, args.repetitions)))
        return
    report = dict(timestamp=datetime.now(timezone.utc).isoformat(), python=sys.executable,
                  platform=platform.platform(), cpu_affinity=sorted(os.sched_getaffinity(0)),
                  cpu_model=next(line.split(':', 1)[1].strip() for line in
                                 Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),
                  repetitions=args.repetitions, cases=[],
                  methodology='Independent native caches per persistent thread. Same fixed number of uncached '
                  'tensor calls at every worker count. Prepared signed Adams operands and reference calculation '
                  'outside timing. Thread/library initialization reported separately. Timed calls include Python '
                  'binding conversions and returned exact maps; exact comparisons occur after timing. Default '
                  '600-second per-call timeout. Each row uses a fresh process. No SQLite or index expansion work.')
    paths = ['common/cpp/lie.cpp', 'common/cpp/lie_bridge.c', 'common/cpp/LiE/exports.map',
             'common/cpp/LiE/liblie.so', 'index/cpp/char_decomposition_cache.cpp',
             'test/benchmark_lie_environments.py', str(Path(native.__file__).relative_to(ROOT))]
    report['sha256'] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    args.output.mkdir(parents=True, exist_ok=True)
    for case in ('su9', 'e8'):
        fingerprint = None
        for workers in args.workers:
            result = subprocess.run([sys.executable, '-B', __file__, '--run-case', case,
                                     '--workers', str(workers), '--calls', str(args.calls),
                                     '--repetitions', str(args.repetitions)], check=True, capture_output=True, text=True)
            row = json.loads(result.stdout)
            if fingerprint is not None and row['result_sha256'] != fingerprint:
                raise AssertionError('worker counts produced different results')
            fingerprint = row['result_sha256']
            report['cases'].append(row)
            (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
            print(f"{case} {workers} workers: {row['median_seconds']:.6f}s, "
                  f"peak {row['peak_rss_kib'] / 1024:.1f} MiB", flush=True)
    lines = ['# Independent LiE environment benchmark', '', report['methodology'], '',
             f"CPU: {report['cpu_model']}. {args.calls} calls/row; medians of {args.repetitions} runs.", '',
             '| Workload | Workers | Median | Speedup vs 1 | Setup | Peak RSS |',
             '|---|---:|---:|---:|---:|---:|']
    for row in report['cases']:
        serial = next((r['median_seconds'] for r in report['cases']
                       if r['case'] == row['case'] and r['workers'] == 1), None)
        speedup = f"{serial / row['median_seconds']:.2f}x" if serial else 'n/a'
        lines.append(f"| {row['case']} | {row['workers']} | {row['median_seconds']:.6f} s | "
                     f"{speedup} | {row['setup_seconds']:.6f} s | {row['peak_rss_kib'] / 1024:.1f} MiB |")
    lines += ['', 'All exact results matched. These are independent LiE calls, not end-to-end index timings.',
              'The existing `project_index` scheduler remains serial.', '',
              'Reproduce:', '', '```sh',
              f"{sys.executable} -B test/benchmark_lie_environments.py --workers "
              + ' '.join(map(str, args.workers))
              + f" --calls {args.calls} --repetitions {args.repetitions}", '```', '']
    (args.output / 'README.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
