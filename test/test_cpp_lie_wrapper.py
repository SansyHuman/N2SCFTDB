"""Exercise native LiE misses, exact arithmetic, recovery and process ownership."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from test.test_cpp_char_decomposition_cache import native, load_backend


def child_compute(path, connection):
    try:
        cache = native.CharacterDecompositionCache(path)
        connection.send(cache.get_decomposition('A2', (1, 0), (0, 1)))
        cache.close()
    except BaseException as exc:
        connection.send(repr(exc))
    finally:
        connection.close()


@unittest.skipIf(native is None, 'build index._index_calculation_cpp first')
class NativeLiEWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cpp = load_backend('cpp')
        cls.python = load_backend('python')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='n2-lie-wrapper-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def cache(self, name='native', **kwargs):
        cache = native.CharacterDecompositionCache(str(self.directory / (name + '.db')), **kwargs)
        self.addCleanup(cache.close)
        return cache

    def test_default_native_misses_work_without_executable(self):
        with patch.dict(os.environ, {'PATH': ''}):
            cache = self.cache()
            self.assertEqual(cache.lie_backend, 'native')
            self.assertEqual(cache.get_decomposition('A1', (1,), (0, 1)),
                             {(0,): -1, (2,): 1})
            self.assertEqual(cache.get_decomposition('A2', (1, 0), (2,)),
                             {(0, 1): 1, (2, 0): 1})

    def test_python_default_uses_native_math_without_string_hooks(self):
        with self.cpp.CharacterDecompositionCache(
            database_path=self.directory / 'public.db', max_workers=1,
        ) as cache, patch.dict(os.environ, {'PATH': ''}), \
                patch.object(cache, '_run_lie', side_effect=AssertionError('subprocess')):
            self.assertEqual(cache.lie_backend, 'native')
            self.assertEqual(cache.get_decomposition('A1', (1,), (1, 1)),
                             {(3,): 1})

    def test_backend_selection_and_python_fallback(self):
        self.assertEqual(self.cache('custom', lie_executable='/missing/lie').lie_backend,
                         'subprocess')
        self.assertEqual(self.cache('process', lie_backend='subprocess').lie_backend,
                         'subprocess')
        forced = self.cache('forced', lie_executable='/missing/lie', lie_backend='native')
        self.assertEqual(forced.get_decomposition('A1', (1,), (0, 1)), {(0,): -1, (2,): 1})
        with self.python.CharacterDecompositionCache(database_path=self.directory / 'python.db') as cache:
            self.assertEqual(cache.lie_backend, 'subprocess')
        with self.assertRaisesRegex(RuntimeError, r'requires the C\+\+'):
            self.python.CharacterDecompositionCache(lie_backend='native')
        with self.assertRaises(ValueError):
            self.cache('invalid', lie_backend='invalid')

    @unittest.skipUnless(shutil.which('lie'), 'reference LiE executable is required')
    def test_native_matches_executable_across_all_simple_families(self):
        compiled, process = self.cache(), self.cache('process', lie_backend='subprocess')
        cases = [('A1', (1,)), ('A2', (1, 0)), ('A3', (0, 1, 0)),
                 ('B2', (0, 1)), ('C3', (1, 0, 0)), ('D4', (1, 0, 0, 0)),
                 ('D5', (0, 0, 0, 0, 1)), ('E6', (1, 0, 0, 0, 0, 0)),
                 ('E7', (0, 0, 0, 0, 0, 0, 1)), ('E8', (0, 0, 0, 0, 0, 0, 0, 1)),
                 ('F4', (0, 0, 0, 1)), ('G2', (1, 0))]
        for algebra, labels in cases:
            for powers in [(0, 1), (2,)]:
                with self.subTest(algebra=algebra, powers=powers):
                    self.assertEqual(compiled.get_decomposition(algebra, labels, powers),
                                     process.get_decomposition(algebra, labels, powers))

    def test_signed_virtual_characters_and_large_binary_coefficients(self):
        cache = self.cache()
        huge = (1 << 20000) + 123
        result = cache.tensor_decompositions('A1', {(1,): huge}, {(1,): -huge})
        self.assertEqual(result, {(0,): -huge * huge, (2,): -huge * huge})
        self.assertEqual(cache.tensor_decompositions('A1', {(0,): -1, (2,): 1}, {(1,): 1}),
                         {(3,): 1})
        self.assertEqual(cache.tensor_decompositions('A1', {}, {(1,): 1}), {})
        self.assertEqual(cache.tensor_decompositions('A1', {(1,): 0}, {(1,): 1}), {})

    def test_failed_calculations_are_not_cached_and_runtime_recovers(self):
        for name, options, message in [
            ('objects', {'max_objects': 3}, 'object limit'),
            ('timeout', {'timeout': 0}, 'timed out'),
        ]:
            with self.subTest(mode=name):
                cache = self.cache(name, **options)
                with self.assertRaisesRegex(RuntimeError, message):
                    cache.get_decomposition('A1', (1,), (0, 1))
                cache.close()
                with sqlite3.connect(self.directory / (name + '.db')) as connection:
                    self.assertEqual(connection.execute(
                        'SELECT count(*) FROM character_decompositions').fetchone()[0], 0)
                self.assertEqual(self.cache(name).get_decomposition('A1', (1,), (0, 1)),
                                 {(0,): -1, (2,): 1})

    def test_timeout_during_weyl_enumeration_recovers(self):
        cache = self.cache(timeout=0.002)
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            cache.calculate_decomposition('E8', (1,) * 8, (0, 1))
        self.assertEqual(self.cache('recovered').get_decomposition('A1', (1,), (0, 1)),
                         {(0,): -1, (2,): 1})

    def test_sampled_deadlines_remain_responsive_and_reset_between_calls(self):
        for timeout in (0.002, 0.01, 0.05):
            with self.subTest(timeout=timeout):
                cache = self.cache(f'deadline-{timeout}', timeout=timeout)
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, 'timed out'):
                    cache.calculate_decomposition('E8', (1,) * 8, (0, 1))
                # Allow scheduler/sanitizer variation, while rejecting a missed
                # deadline that lets this expensive operation run unchecked.
                self.assertLess(time.monotonic() - start, timeout + 0.5)
                unlimited = self.cache(f'unlimited-{timeout}', timeout=-1)
                self.assertEqual(unlimited.calculate_decomposition('A1', (1,), (0, 1)),
                                 {(0,): -1, (2,): 1})
                self.assertEqual(self.cache(f'normal-{timeout}').calculate_decomposition(
                    'A1', (1,), (0, 1)), {(0,): -1, (2,): 1})

    def test_concurrent_independent_clients_keep_group_state_separate(self):
        def compute(worker):
            cache = native.CharacterDecompositionCache(str(self.directory / f'{worker}.db'))
            try:
                for _ in range(20):
                    if worker % 2:
                        self.assertEqual(cache.tensor_decompositions('A2', {(1, 0): 1}, {(0, 1): 1}),
                                         {(0, 0): 1, (1, 1): 1})
                    else:
                        self.assertEqual(cache.calculate_decomposition('C2', (1, 0), (0, 1)),
                                         {(0, 0): -1, (0, 1): -1, (2, 0): 1})
            finally:
                cache.close()
        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(compute, range(8)))

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(), 'requires fork')
    def test_fork_after_native_initialization(self):
        cache = self.cache()
        cache.calculate_decomposition('A1', (1,), (0, 1))
        context = multiprocessing.get_context('fork')
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=child_compute,
                                  args=(str(self.directory / 'child.db'), child))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(15), 'native call in child hung')
            self.assertEqual(parent.recv(), {(0, 1): -1, (2, 0): 1})
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()


if __name__ == '__main__':
    unittest.main()
