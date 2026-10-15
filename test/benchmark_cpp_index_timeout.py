"""Measure native expansion timeout overhead, excluding output/launch timing.

Use Sage's Python and a saved pre-change driver as --baseline when available.
Every printed term is compared byte-for-byte outside the native timing region.
No FORM cache, character decomposition, or database is used.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile

from benchmark_cpp_index_expand import ROOT, benchmark_cases, compile_driver, cpp_input


def measure(executable, payload, workers, timeout=None):
    command = [str(executable), '--workers', str(workers)]
    if timeout is not None:
        command += ['--timeout', str(timeout)]
    result = subprocess.run(command, input=payload, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr)
    metadata = json.loads(result.stderr)
    return metadata, hashlib.sha256(result.stdout.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--repetitions', type=int, default=7)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 4])
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'output/benchmarks/cpp_index_timeout_20261001')
    args = parser.parse_args()
    if args.repetitions < 1 or any(worker <= 0 for worker in args.workers):
        parser.error('repetitions and worker counts must be positive')
    args.output.mkdir(parents=True, exist_ok=True)
    report = dict(created_utc=datetime.now(timezone.utc).isoformat(), repetitions=args.repetitions,
                  cpu_affinity=sorted(os.sched_getaffinity(0)), cases=[],
                  methodology='Native function time includes validation, worker management, '
                  'exact expansion, output construction and sorting; it excludes process '
                  'startup, text input/output and hashing. One warmup per mode, followed by '
                  'rotated mode order. Exact sorted output fingerprints match every run. '
                  'Default timeout is 600 seconds; -1 is unlimited.')
    report['source_sha256'] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                               for path in ['index/cpp/index_expand.cpp', 'index/cpp/index_expand.hpp',
                                            'test/cpp/index_expand_driver.cpp', 'test/benchmark_cpp_index_timeout.py']}
    if args.baseline:
        report['baseline_executable'] = str(args.baseline)
        report['baseline_sha256'] = hashlib.sha256(args.baseline.read_bytes()).hexdigest()
    cases = benchmark_cases()
    selected = [('simple', 18, cases['su2_four_full_hypers']),
                ('trifundamental', 18, cases['trifundamental']),
                ('trifundamental', 20, cases['trifundamental']),
                ('large_output', 2, (4201, (), {(i, i): (-1 if i % 2 else 1) * (i + 1)
                                             for i in range(4201)}))]
    with tempfile.TemporaryDirectory(prefix='index-timeout-benchmark-') as temporary:
        current, report['build'] = compile_driver(Path(temporary))
        modes = {'default': (current, None), 'unlimited': (current, -1.0)}
        if args.baseline:
            modes['before'] = (args.baseline, None)
        names = tuple(modes)
        for name, order, (characters, vectors, matter) in selected:
            payload = cpp_input(order, characters, vectors, matter)
            for workers in args.workers:
                samples = {mode: [] for mode in names}
                reference = None
                terms = None
                for mode, (executable, timeout) in modes.items():
                    record, fingerprint = measure(executable, payload, workers, timeout)
                    if reference is None:
                        reference, terms = fingerprint, record['term_count']
                    assert fingerprint == reference and record['term_count'] == terms
                for repetition in range(args.repetitions):
                    shift = repetition % len(names)
                    for mode in names[shift:] + names[:shift]:
                        executable, timeout = modes[mode]
                        record, fingerprint = measure(executable, payload, workers, timeout)
                        assert fingerprint == reference and record['term_count'] == terms
                        samples[mode].append(record['expansion_seconds'])
                medians = {mode: statistics.median(values) for mode, values in samples.items()}
                case = dict(name=name, order=order, workers=workers, term_count=terms,
                            output_sha256=reference, samples_seconds=samples, medians_seconds=medians,
                            timeout_overhead_percent=100 * (medians['default'] / medians['unlimited'] - 1))
                report['cases'].append(case)
                print(json.dumps(case), flush=True)
                (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# C++ index expansion timeout overhead', '', report['methodology'], '',
             f'{args.repetitions} measured repetitions per mode; medians shown. '
             'Negative overhead reflects measurement variation.', '',
             '| Input | Cutoff | Workers | Terms | Before | Unlimited | Default timeout | Timeout overhead |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for case in report['cases']:
        median = case['medians_seconds']
        before = f"{median['before']:.6f} s" if 'before' in median else 'n/a'
        lines.append(f"| {case['name']} | {case['order']} | {case['workers']} | {case['term_count']} | "
                     f"{before} | {median['unlimited']:.6f} s | {median['default']:.6f} s | "
                     f"{case['timeout_overhead_percent']:.2f}% |")
    lines += ['', 'No persistent data was accessed. The internal unordered helper preserves '
              'canonical character tuples while skipping only the final outer-vector sort. '
              'The public functions retain the previous exact sorted output.', '',
              'See `results.json` for raw samples, fingerprints, compiler command and source hashes.', '']
    (args.output / 'README.md').write_text('\n'.join(lines))
    print('\n'.join(lines), flush=True)


if __name__ == '__main__':
    main()
