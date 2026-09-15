"""Benchmark the current character cache against a frozen git revision.

Run with Sage Python. Cache directories are fresh for each backend and trial;
'warm' uses a new client against that trial's persisted data. Comparisons use
exact decompositions or Laurent-polynomial coefficient dictionaries. The
projection suite excludes the shared FORM expansion; the full suite includes it.
With multiple workers, lie_calls counts parent-process calls only.
"""
from pathlib import Path
from statistics import median
import argparse
import importlib
import json
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from index import char_decomposition_cache as candidate_cache
from index import n2_theory_index as candidate_index


def load_baseline(revision):
    directory = tempfile.TemporaryDirectory(prefix='sci-character-baseline-')
    path = Path(directory.name)
    for source_name, module_name in (
        ('char_decomposition_cache', 'original_cache'),
        ('n2_theory_index', 'original_index'),
    ):
        source = subprocess.run(
            ['git', 'show', f'{revision}:index/{source_name}.py'],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout
        if module_name == 'original_index':
            source = source.replace('from index.char_decomposition_cache import (',
                                    'from original_cache import (')
        (path / f'{module_name}.py').write_text(source)
    sys.path.insert(0, str(path))
    return (importlib.import_module('original_cache'),
            importlib.import_module('original_index'), directory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--baseline-ref', default='d4d103a')
    parser.add_argument('--baseline-label', default='json')
    parser.add_argument('--candidate-label', default='sqlite')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--suite', choices=['projection', 'full', 'decomposition'], default='projection')
    args = parser.parse_args()
    destination = args.output_directory.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    if args.rounds < 1 or args.workers < 1:
        parser.error('rounds and workers must be positive')
    if args.baseline_label == args.candidate_label:
        parser.error('backend labels must be distinct')
    original_cache, original_index, baseline_directory = load_baseline(args.baseline_ref)

    def timed(operation):
        def expired(*unused):
            raise TimeoutError('benchmark operation exceeded 55 seconds')
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, 55)
        start = time.perf_counter()
        try:
            value = operation()
        finally:
            elapsed = time.perf_counter() - start
            signal.setitimer(signal.ITIMER_REAL, 0)
        return value, elapsed

    def sqcd(rank):
        return {'algebra': f'A{rank}', 'hypermultiplets': [
            {'representation': 'fundamental', 'number': 2 * (rank + 1), 'kind': 'full'}]}

    cases = [
        ('A1-t18', sqcd(1), 18), ('A2-t12', sqcd(2), 12),
        ('A8-t18', sqcd(8), 18), ('A16-t12', sqcd(16), 12),
        ('A32-t8', sqcd(32), 8),
        ('A1xA1-t12', {'gauge_groups': [{'id': 'a', 'algebra': 'A1'}, {'id': 'b', 'algebra': 'A1'}],
                      'hypermultiplets': [{'representations': {'a': 'fundamental', 'b': 'fundamental'},
                                           'number': 2, 'kind': 'full'}]}, 12),
        ('A1xA1xA1-half-t10', {'gauge_groups': [{'id': x, 'algebra': 'A1'} for x in 'abc'],
                              'hypermultiplets': [{'representations': {x: 'fundamental' for x in 'abc'},
                                                   'number': 2, 'kind': 'half'}]}, 10),
    ]
    if args.suite != 'projection':
        cases = [case for case in cases if case[0] == 'A8-t18']

    for name, data, order in cases:
        print(json.dumps({'case': name, 'stage': 'preparing'}), flush=True)
        factors, hypers = original_index._parse_input(data)
        specs, vectors, matter = original_index._character_basis(factors, hypers)
        program = original_index._build_form_program(order, len(specs), vectors, matter)
        output = original_index.run_form(program, form_executable='form', timeout=55)
        terms = original_index._parse_form_output(output)
        requests = sorted({(factors[specs[c][0]].algebra.cartan_type, specs[c][1], powers)
                           for term in terms for c, powers in term.characters})
        reference = None
        for round_number in range(args.rounds):
            backends = [(args.baseline_label, original_cache, original_index),
                        (args.candidate_label, candidate_cache, candidate_index)]
            if round_number % 2:
                backends.reverse()
            for backend, cache_module, index_module in backends:
                directory = destination / f'{name}-{round_number}-{backend}'
                for phase in ('cold', 'warm'):
                    calls = []
                    class MeasuredCache(cache_module.CharacterDecompositionCache):
                        def _run_lie(self, expressions):
                            calls.append(len(expressions))
                            return super()._run_lie(expressions)
                    index_module.CharacterDecompositionCache = MeasuredCache
                    def calculate():
                        if args.suite == 'full':
                            # Historical baselines retain their original directory API.
                            cache_options = (
                                {'char_cache_database_path': directory / 'char_decomposition_cache.db'}
                                if index_module is candidate_index else {'cache_directory': directory}
                            )
                            return index_module.calculate_index(data, order, **cache_options,
                                                                processes=args.workers, timeout=50).dict()
                        cache = MeasuredCache(directory, max_workers=args.workers, timeout=50)
                        try:
                            if args.suite == 'decomposition':
                                return cache.get_decompositions(requests)
                            projected = index_module._project_terms(terms, factors, specs, cache)
                            return index_module._to_sage_polynomial(projected).dict()
                        finally:
                            if hasattr(cache, 'close'):
                                cache.close()
                    try:
                        result, seconds = timed(calculate)
                        if reference is None:
                            reference = result
                        assert result == reference, (name, backend, phase, 'result mismatch')
                        record = dict(case=name, backend=backend, phase=phase, round=round_number,
                                      seconds=seconds, workers=args.workers, lie_calls=len(calls), lie_expressions=sum(calls),
                                      result_entries=len(result), status='equal')
                    except Exception as exc:
                        record = dict(case=name, backend=backend, phase=phase, round=round_number,
                                      workers=args.workers, error=f'{type(exc).__name__}: {exc}', status='failed')
                        records.append(record)
                        (destination / 'results.json').write_text(json.dumps(records, indent=2))
                        raise
                    records.append(record)
                    print(json.dumps(record), flush=True)
                    (destination / 'results.json').write_text(json.dumps(records, indent=2))
        for phase in ('cold', 'warm'):
            times = {backend: median(r['seconds'] for r in records
                                     if r['case'] == name and r['backend'] == backend and r['phase'] == phase)
                     for backend in (args.baseline_label, args.candidate_label)}
            print(json.dumps(dict(case=name, phase=phase, median=times, speedup=times[args.baseline_label]/times[args.candidate_label])), flush=True)

    baseline_directory.cleanup()


if __name__ == "__main__":
    main()
