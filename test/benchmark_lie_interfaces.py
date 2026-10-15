"""Compare consecutive uncached native LiE calls and executable calls.

Run with the interpreter used to build index._char_cache_cpp. The third mode
sends the same commands through one executable process to amortize startup.
Only JSON/Markdown reports are retained; no character-cache database is opened.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from index import _char_cache_cpp as native

MODES = ('shared_library', 'executable_per_call', 'executable_one_batch')


def adams(algebra, labels, power):
    return dict(kind='adams', algebra=algebra, labels=labels, power=power,
                expression=f'Adams({power},[{",".join(map(str, labels))}],{algebra})')


def tensor(algebra, left, right):
    return dict(kind='tensor', algebra=algebra, left=left, right=right,
                expression=f'tensor({native.format_lie_decomposition(left)},'
                           f'{native.format_lie_decomposition(right)},{algebra})')


def calculate(cache, operation):
    if operation['kind'] == 'adams':
        power = operation['power']
        return cache.calculate_decomposition(operation['algebra'], operation['labels'],
                                             (0,) * (power - 1) + (1,))
    return cache.tensor_decompositions(operation['algebra'], operation['left'], operation['right'])


def workloads(cache):
    def psi(algebra, labels, power):
        return calculate(cache, adams(algebra, labels, power))
    a1 = [adams('A1', (1,), n) for n in (2, 3, 4)]
    a1 += [tensor('A1', {(1,): 1}, {(1,): 1}),
           tensor('A1', psi('A1', (1,), 2), {(1,): 1}),
           tensor('A1', psi('A1', (1,), 3), psi('A1', (1,), 3))]
    mixed = [adams('A2', (1, 1), n) for n in (2, 3, 4)]
    mixed += [adams('C2', (2, 0), 3),
              tensor('A2', psi('A2', (1, 1), 2), psi('A2', (1, 1), 2)),
              tensor('C2', psi('C2', (2, 0), 2), psi('C2', (2, 0), 2))]
    adjoint = (1, 0, 0, 0, 0, 0, 0, 1)
    su9 = [adams('A8', adjoint, n) for n in (3, 4)]
    su9 += [tensor('A8', psi('A8', adjoint, 3), psi('A8', adjoint, n)) for n in (3, 4)]
    adjoint = (0, 0, 0, 0, 0, 0, 0, 1)
    e8 = [adams('E8', adjoint, n) for n in (2, 3)]
    e8 += [tensor('E8', psi('E8', adjoint, n), psi('E8', adjoint, n)) for n in (2, 3)]
    return [('SU2 small', a1, 1200), ('SU3/Sp4 adjoint', mixed, 600),
            ('SU9 adjoint tensors', su9, 60), ('E8 adjoint tensors', e8, 24)]


def run_batch(cache, operations):
    commands = []
    for index, operation in enumerate(operations):
        commands.extend((operation['expression'], f'print("N2_LIE_BENCH_END_{index}")'))
    lines = cache.run_lie(commands)
    results, polynomial = [], []
    for line in lines:
        if line == f'N2_LIE_BENCH_END_{len(results)}':
            rank = int(operations[len(results)]['algebra'][1:])
            results.append(native.parse_lie_decomposition(''.join(polynomial), rank))
            polynomial = []
        else:
            polynomial.append(line)
    if polynomial or len(results) != len(operations):
        raise RuntimeError('batch output markers do not match the command sequence')
    return results


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--lie-executable', default=shutil.which('lie'))
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'output/benchmarks/lie_interfaces_20261001')
    parser.add_argument('--cpu', type=int, help='Pin this benchmark and its children to one allowed CPU')
    args = parser.parse_args()
    if args.repetitions < 1 or not args.lie_executable:
        parser.error('positive repetitions and a usable LiE executable are required')
    if args.cpu is not None:
        os.sched_setaffinity(0, {args.cpu})
    args.output.mkdir(parents=True, exist_ok=True)
    report = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
                  python=sys.executable, python_version=sys.version, platform=platform.platform(),
                  cpu_affinity=sorted(os.sched_getaffinity(0)), repetitions=args.repetitions,
                  executable=args.lie_executable, cases=[],
                  methodology='Sequential calls, exact owning maps returned, no database access. '
                  'Operands are prepared before timing. Timing includes binding conversions, '
                  'native allocation/recovery, or subprocess startup/IPC/formatting/parsing. '
                  'Batch mode also includes command construction and marker parsing. '
                  'Verification is outside timing; mode order rotates between repetitions. '
                  'The executable is the installed LiE; its compiler flags may differ from '
                  'the local shared library. Batch mode is a startup-amortization control, '
                  'not the currently implemented subprocess calculation API.')
    report['cpu_model'] = next((line.split(':', 1)[1].strip() for line in
                                Path('/proc/cpuinfo').read_text().splitlines()
                                if line.startswith('model name')), 'unknown')
    paths = ['common/cpp/LiE/liblie.so', 'common/cpp/lie.cpp', 'common/cpp/lie_bridge.c',
             'index/cpp/char_decomposition_cache.cpp', 'test/benchmark_lie_interfaces.py']
    report['sha256'] = {path: file_hash(ROOT / path) for path in paths}
    report['sha256']['extension'] = file_hash(native.__file__)
    report['sha256']['executable_launcher'] = file_hash(args.lie_executable)
    installed_binary = Path('/usr/lib/lie/lie')
    if installed_binary.is_file():
        report['sha256']['installed_lie_binary'] = file_hash(installed_binary)
    with tempfile.TemporaryDirectory(prefix='n2-lie-benchmark-') as temporary:
        shared = native.CharacterDecompositionCache(str(Path(temporary) / 'native-unused.db'))
        process = native.CharacterDecompositionCache(str(Path(temporary) / 'exe-unused.db'),
                                                    lie_executable=args.lie_executable,
                                                    lie_backend='subprocess')
        try:
            for name, cycle, count in workloads(shared):
                expected_cycle = [calculate(shared, op) for op in cycle]
                assert [calculate(process, op) for op in cycle] == expected_cycle
                assert run_batch(process, cycle) == expected_cycle
                operations = [cycle[i % len(cycle)] for i in range(count)]
                expected = [expected_cycle[i % len(cycle)] for i in range(count)]
                samples = {mode: [] for mode in MODES}
                for repetition in range(args.repetitions):
                    order = MODES[repetition % 3:] + MODES[:repetition % 3]
                    for mode in order:
                        start = time.perf_counter()
                        if mode == 'executable_one_batch':
                            actual = run_batch(process, operations)
                        else:
                            cache = shared if mode == 'shared_library' else process
                            actual = [calculate(cache, op) for op in operations]
                        elapsed = time.perf_counter() - start
                        if actual != expected:
                            raise AssertionError(f'nonmatching result: {name}, {mode}')
                        samples[mode].append(elapsed)
                        print(f'{name}: round {repetition + 1} {mode} {elapsed:.6f}s', flush=True)
                case = dict(name=name, calls_per_round=count, cycle_length=len(cycle),
                            operation_expressions=[op['expression'] for op in cycle],
                            result_terms=[len(result) for result in expected_cycle],
                            result_sha256=[hashlib.sha256(native.encode_decomposition(result).encode()).hexdigest()
                                           for result in expected_cycle],
                            samples_seconds=samples, medians_seconds={mode: statistics.median(values)
                                                                       for mode, values in samples.items()})
                medians = case['medians_seconds']
                case['speedup_over_per_call_executable'] = medians['executable_per_call'] / medians['shared_library']
                case['speedup_over_batched_executable'] = medians['executable_one_batch'] / medians['shared_library']
                report['cases'].append(case)
                (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
            assert not list(Path(temporary).glob('*.db')), 'unexpected cache database access'
        finally:
            shared.close()
            process.close()
    rows = ['# Consecutive LiE interface benchmark', '',
            f"CPU: {report['cpu_model']}. Affinity: {report['cpu_affinity']}. "
            f'{args.repetitions} repetitions; medians shown.', '', report['methodology'], '',
            '| Workload | Calls/round | Shared library | Executable/call | Executable/batch | Speedup vs per-call |',
            '|---|---:|---:|---:|---:|---:|']
    for case in report['cases']:
        median = case['medians_seconds']
        rows.append(f"| {case['name']} | {case['calls_per_round']} | "
                    f"{median['shared_library']:.6f} s | {median['executable_per_call']:.6f} s | "
                    f"{median['executable_one_batch']:.6f} s | "
                    f"{case['speedup_over_per_call_executable']:.2f}x |")
    rows += ['', 'All returned decompositions matched exactly, including signed coefficients. '
             'No production database was accessed. No application code was changed.', '',
             'Reproduce with the Python used to build the extension:', '', '```sh',
             f'{sys.executable} -B test/benchmark_lie_interfaces.py --repetitions {args.repetitions}'
             + (f' --cpu {args.cpu}' if args.cpu is not None else ''), '```', '']
    (args.output / 'README.md').write_text('\n'.join(rows))
    print('\n'.join(rows), flush=True)


if __name__ == '__main__':
    main()
