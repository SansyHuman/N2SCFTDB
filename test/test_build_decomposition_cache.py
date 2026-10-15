"""Check complete Adams-order builds, resumption, process workers and the CLI."""

from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.math_utils import frobenius_solve
from index import char_decomposition_cache as module
from index.char_decomposition_cache import CharacterDecompositionCache as Cache
from index.char_decomposition_cache import build_decomposition_cache
from test.test_character_decomposition_cache import su2_fundamental_adams_product


def read_rows(path):
    with sqlite3.connect(path) as connection:
        return {
            (algebra, tuple(json.loads(labels)), tuple(json.loads(powers))):
                Cache._decode_decomposition(payload)
            for algebra, labels, powers, payload in connection.execute(
                'SELECT algebra, dynkin_labels, adams_powers, terms_json '
                'FROM character_decompositions'
            )
        }


class BuilderInputTests(unittest.TestCase):
    def test_invalid_inputs_do_not_create_a_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.db'
            for args, kwargs in [
                (('bad', (1,), 4), {}), (('A2', (1,), 4), {}),
                (('A1', (-1,), 4), {}), (('A1', (1.5,), 4), {}),
                (('A1', (1,), 0), {}), (('A1', (1,), -1), {}),
                (('A1', (1,), 2.5), {}), (('A1', (1,), True), {}),
                (('A1', (1,), 4), {'processes': 0}),
                (('A1', (1,), 4), {'processes': -1}),
                (('A1', (1,), 4), {'processes': 2.5}),
                (('A1', (1,), 4), {'processes': True}),
                (('A1', (1,), 4), {'cache_directory': directory}),
            ]:
                with self.subTest(args=args, kwargs=kwargs):
                    with self.assertRaises((TypeError, ValueError)):
                        build_decomposition_cache(*args, database_path=path, **kwargs)
                    self.assertFalse(path.exists())

    def test_default_and_directory_paths_and_first_order_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            default = Path(directory) / 'default.db'
            with patch.object(module, 'DEFAULT_CHAR_CACHE_DATABASE', default):
                self.assertEqual(build_decomposition_cache(
                    'a_2', iter([1, 0]), 1, lie_executable='must-not-run', processes=3,
                ), {1: 1})
                selected = Path(directory) / 'selected'
                build_decomposition_cache('A2', [1, 0], 1, cache_directory=selected,
                                          lie_executable='must-not-run', processes=1)
                self.assertEqual(read_rows(default), read_rows(selected / default.name))

    def test_trivial_representation_needs_no_lie_or_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.db'
            with patch.object(module, 'ProcessPoolExecutor', side_effect=AssertionError('pool')):
                counts = build_decomposition_cache(
                    'A2', (0, 0), 6, database_path=path, processes=3,
                    lie_executable='must-not-run',
                )
            self.assertEqual(counts, {1: 1, 2: 2, 3: 3, 4: 5, 5: 7, 6: 11})
            self.assertEqual(len(read_rows(path)), 29)
            self.assertTrue(all(value == {(0, 0): 1} for value in read_rows(path).values()))


@unittest.skipUnless(shutil.which('lie'), 'LiE is required')
class BuildDecompositionCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / 'cache.db'

    def test_complete_enumeration_exact_weights_and_minimum_adams_calls(self):
        expressions = []
        original = Cache._run_lie

        def execute(cache, code):
            expressions.extend(code)
            return original(cache, code)

        reports = []

        def progress(order, total, computed):
            with sqlite3.connect(self.path) as connection:
                self.assertEqual(connection.execute(
                    'SELECT count(*) FROM character_decompositions WHERE adams_order=?',
                    (order,),
                ).fetchone()[0], total)
            reports.append((order, total, computed, os.getpid()))

        with patch.object(Cache, '_run_lie', execute), \
                patch('common.math_utils.frobenius_solve', wraps=frobenius_solve) as solve:
            counts = build_decomposition_cache('A1', (1,), 6, database_path=self.path,
                                               processes=1, progress=progress,
                                               lie_backend='subprocess')
        self.assertEqual(counts, {1: 1, 2: 2, 3: 3, 4: 5, 5: 7, 6: 11})
        self.assertEqual([(tuple(call.args[0]), call.args[1]) for call in solve.call_args_list],
                         [(tuple(range(1, n + 1)), n) for n in range(1, 7)])
        self.assertEqual([code for code in expressions if code.startswith('Adams(')],
                         [f'Adams({n},[1],A1)' for n in range(2, 7)])
        self.assertEqual(sum(code.startswith('tensor(') for code in expressions), 23)
        self.assertEqual(reports, [(n, count, count, os.getpid()) for n, count in counts.items()])
        rows = read_rows(self.path)
        self.assertEqual(len(rows), 29)
        for (_, _, powers), value in rows.items():
            self.assertEqual(len(powers), sum(j * n for j, n in enumerate(powers, start=1)))
            self.assertEqual(value, su2_fundamental_adams_product(powers))

    def test_parallel_matches_serial_and_reuses_one_pool(self):
        for algebra, labels in [('A1', (1,)), ('A2', (1, 0)),
                                ('C2', (1, 0)), ('G2', (1, 0))]:
            with self.subTest(algebra=algebra):
                serial = self.directory / f'{algebra}-serial.db'
                parallel = self.directory / f'{algebra}-parallel.db'
                counts = build_decomposition_cache(algebra, labels, 5,
                                                    database_path=serial, processes=1)
                events = []
                with patch.object(module, 'ProcessPoolExecutor', wraps=ProcessPoolExecutor) as pool:
                    self.assertEqual(build_decomposition_cache(
                        algebra, labels, 5, database_path=parallel, processes=3,
                        progress=lambda *event: events.append((*event, os.getpid())),
                    ), counts)
                pool.assert_called_once()
                self.assertEqual(events, [(n, count, count, os.getpid()) for n, count in counts.items()])
                self.assertEqual(read_rows(serial), read_rows(parallel))

    @unittest.skipUnless(os.name == 'posix', 'executable wrapper requires POSIX')
    def test_parallel_workers_do_not_repeat_lower_order_adams_calls(self):
        log = self.directory / 'lie-calls.jsonl'
        wrapper = self.directory / 'logged-lie'
        wrapper.write_text(
            f'#!{sys.executable}\n'
            'import fcntl, json, os, subprocess, sys\n'
            'code = sys.stdin.read()\n'
            f'with open({str(log)!r}, "a") as handle:\n'
            '    fcntl.flock(handle, fcntl.LOCK_EX)\n'
            '    handle.write(json.dumps({"worker": os.getppid(), "code": code}) + "\\n")\n'
            '    handle.flush()\n'
            f'result = subprocess.run([{shutil.which("lie")!r}], input=code, text=True, capture_output=True)\n'
            'sys.stdout.write(result.stdout)\n'
            'sys.stderr.write(result.stderr)\n'
            'sys.exit(result.returncode)\n'
        )
        wrapper.chmod(0o700)
        build_decomposition_cache('A1', (1,), 6, database_path=self.path,
                                  processes=3, lie_executable=str(wrapper))
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        expressions = [call['code'].strip() for call in calls]
        self.assertCountEqual([code for code in expressions if code.startswith('Adams(')],
                              [f'Adams({n},[1],A1)' for n in range(2, 7)])
        self.assertEqual(sum(code.startswith('tensor(') for code in expressions), 23)
        self.assertGreaterEqual(len({call['worker'] for call in calls}), 2)

    def test_existing_rows_skip_lie_decoding_and_worker_startup(self):
        expected = build_decomposition_cache('A1', (1,), 6, database_path=self.path, processes=1)
        events = []
        with patch.object(Cache, '_decode_decomposition', side_effect=AssertionError('decoded hit')), \
                patch.object(module, 'ProcessPoolExecutor', side_effect=AssertionError('pool')):
            actual = build_decomposition_cache(
                'A1', (1,), 6, database_path=self.path, lie_executable='must-not-run',
                processes=3, progress=lambda *event: events.append(event),
            )
        self.assertEqual(actual, expected)
        self.assertEqual(events, [(n, count, 0) for n, count in expected.items()])

    def test_resume_fills_only_missing_entries_and_new_orders(self):
        build_decomposition_cache('A1', (1,), 4, database_path=self.path, processes=1)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                'DELETE FROM character_decompositions WHERE adams_powers IN (?,?)',
                ('[0,0,0,1]', '[1,0,1,0]'),
            )
        expressions = []
        original = Cache._run_lie

        def execute(cache, code):
            expressions.extend(code)
            return original(cache, code)

        events = []
        with patch.object(Cache, '_run_lie', execute):
            build_decomposition_cache('A1', (1,), 6, database_path=self.path,
                                      processes=1, progress=lambda *event: events.append(event),
                                      lie_backend='subprocess')
        self.assertEqual(events, [(1, 1, 0), (2, 2, 0), (3, 3, 0),
                                  (4, 5, 2), (5, 7, 7), (6, 11, 11)])
        self.assertEqual([code for code in expressions if code.startswith('Adams(')],
                         [f'Adams({n},[1],A1)' for n in range(4, 7)])
        self.assertEqual(len(read_rows(self.path)), 29)

    def test_completed_orders_survive_stopping_a_parallel_build(self):
        def stop(order, total, computed):
            if order == 3:
                raise RuntimeError('stop after three orders')

        with self.assertRaisesRegex(RuntimeError, 'stop after three'):
            build_decomposition_cache('A1', (1,), 6, database_path=self.path,
                                      processes=3, progress=stop)
        self.assertEqual(len(read_rows(self.path)), 6)
        self.assertEqual(build_decomposition_cache(
            'A1', (1,), 3, database_path=self.path, processes=3, lie_executable='must-not-run',
        ), {1: 1, 2: 2, 3: 3})
        build_decomposition_cache('A1', (1,), 6, database_path=self.path, processes=3)
        for (_, _, powers), value in read_rows(self.path).items():
            self.assertEqual(value, su2_fundamental_adams_product(powers))

    def test_lie_failure_does_not_store_false_decompositions(self):
        original = Cache._run_lie

        def execute(cache, code):
            if code == ['Adams(3,[1],A1)']:
                raise RuntimeError('simulated LiE failure')
            return original(cache, code)

        with patch.object(Cache, '_run_lie', execute), self.assertRaisesRegex(RuntimeError, 'simulated'):
            build_decomposition_cache('A1', (1,), 5, database_path=self.path, processes=1,
                                      lie_backend='subprocess')
        self.assertEqual(len(read_rows(self.path)), 3)
        self.assertEqual(build_decomposition_cache(
            'A1', (1,), 2, database_path=self.path, processes=1, lie_executable='must-not-run',
        ), {1: 1, 2: 2})

    def test_cli_direct_file_and_module_resume(self):
        command = [sys.executable, '-B', str(ROOT / 'index/char_decomposition_cache.py'),
                   'A1', '--dynkin-labels', '1', '--max-adams-order', '5', '--processes', '3',
                   '--cache-database', str(self.path)]
        result = subprocess.run(command, cwd=self.directory, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Cache ready: 18 decompositions', result.stdout)
        resumed = subprocess.run(
            [sys.executable, '-B', '-m', 'index.char_decomposition_cache', 'A1',
             '--dynkin-labels', '1', '--max-order', '5', '--processes', '3',
             '--cache-database', str(self.path), '--lie-executable', 'must-not-run'],
            cwd=ROOT, text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn('Adams order 5: 7 cached (0 computed, 7 reused)', resumed.stdout)
        self.assertEqual(len(read_rows(self.path)), 18)

    def test_cli_reports_parallel_worker_failure(self):
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'index/char_decomposition_cache.py'), 'A1',
             '--dynkin-labels', '1', '--max-order', '2', '--processes', '2',
             '--cache-database', str(self.path), '--lie-executable', 'missing-lie'],
            cwd=self.directory, text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('error:', result.stderr)
        self.assertIn('was not found', result.stderr)
        self.assertEqual(len(read_rows(self.path)), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
