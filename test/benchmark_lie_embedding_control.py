"""Isolate embedding/deadline overhead using the same shared LiE core."""

import argparse
import json
import os
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import tempfile
import time

from benchmark_lie_interfaces import ROOT, native, workloads, calculate, run_batch, file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu', type=int, default=15)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'output/benchmarks/lie_timeout_sampling_20261001')
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('repetitions must be positive')
    # Match the main benchmark's affinity and rotate measurement order.
    # The executable and launcher remain temporary.
    os.sched_setaffinity(0, {args.cpu})
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    library = ROOT / 'common/cpp/LiE'
    report = dict(repetitions=args.repetitions, cpu_affinity=sorted(os.sched_getaffinity(0)), cases=[],
                  source_sha256=file_hash(ROOT / 'test/cpp/lie_cli_driver.c'),
                  library_sha256=file_hash(library / 'liblie.so'),
                  note='CLI and embedded calls use the identical local liblie.so. '
                  'Unlimited mode suppresses cooperative deadline polling only; '
                  'object limits and allocation tracking remain enabled.')
    with tempfile.TemporaryDirectory(prefix='n2-lie-control-') as temporary:
        directory = Path(temporary)
        cli = directory / 'lie-shared-cli'
        command = ['gcc', '-O3', str(ROOT / 'test/cpp/lie_cli_driver.c'),
                   '-L', str(library), f'-Wl,-rpath,{library}', '-llie', '-o', str(cli)]
        subprocess.run(command, check=True)
        launcher = directory / 'lie-control'
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(cli)) + ' initfile /usr/lib/lie\n')
        launcher.chmod(0o700)
        report['compiler_command'] = command
        modes = {
            'shared_library': native.CharacterDecompositionCache(str(directory / 'a.db')),
            'shared_library_unlimited': native.CharacterDecompositionCache(str(directory / 'b.db'), timeout=-1),
            'same_library_cli_per_call': native.CharacterDecompositionCache(
                str(directory / 'c.db'), lie_executable=str(launcher), lie_backend='subprocess'),
        }
        order = tuple(modes) + ('same_library_cli_batch',)
        try:
            for name, cycle, count in workloads(modes['shared_library'])[2:]:
                expected_cycle = [calculate(modes['shared_library'], op) for op in cycle]
                operations = [cycle[i % len(cycle)] for i in range(count)]
                expected = [expected_cycle[i % len(cycle)] for i in range(count)]
                samples = {mode: [] for mode in order}
                for repetition in range(args.repetitions):
                    for mode in order[repetition % 4:] + order[:repetition % 4]:
                        start = time.perf_counter()
                        if mode == 'same_library_cli_batch':
                            actual = run_batch(modes['same_library_cli_per_call'], operations)
                        else:
                            actual = [calculate(modes[mode], op) for op in operations]
                        elapsed = time.perf_counter() - start
                        assert actual == expected, (name, mode)
                        samples[mode].append(elapsed)
                        print(f'{name}: round {repetition + 1} {mode} {elapsed:.6f}s', flush=True)
                report['cases'].append(dict(name=name, calls_per_round=count, samples_seconds=samples,
                                            medians_seconds={mode: statistics.median(values)
                                                             for mode, values in samples.items()}))
            assert not list(directory.glob('*.db'))
        finally:
            for cache in modes.values():
                cache.close()
    (output / 'same_library_control.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['cases'], indent=2), flush=True)


if __name__ == '__main__':
    main()
