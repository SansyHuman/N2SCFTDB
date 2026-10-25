"""Exercise native LiE misses, exact arithmetic, recovery and process ownership."""

from concurrent.futures import ThreadPoolExecutor
import ctypes
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
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

def inherited_cache_compute(cache, connection):
    try:
        connection.send(cache.calculate_decomposition('A2', (1, 0), (0, 1)))
        cache.close()
    except BaseException as exc:
        connection.send(repr(exc))
    finally:
        connection.close()


def environment_mappings():
    """Identify distinct loaded library images, independent of mapping count."""
    return {tuple(line.split()[3:5]) for line in Path('/proc/self/maps').read_text().splitlines()
            if '/memfd:n2scftdb-lie ' in line}


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

    def test_environment_is_lazy_reused_and_released_on_close(self):
        before = environment_mappings()
        cache, other = self.cache(), self.cache('other')
        request = ('A1', (1,), (0, 1))
        expected = {(0,): -1, (2,): 1}
        cache.write_decompositions([(request, expected)])
        self.assertEqual(cache.get_decomposition(*request), expected)
        self.assertEqual(environment_mappings(), before, 'cache hit loaded LiE')
        for _ in range(5):
            self.assertEqual(cache.calculate_decomposition(*request), expected)
            self.assertEqual(len(environment_mappings() - before), 1)
        self.assertEqual(other.calculate_decomposition(*request), expected)
        self.assertEqual(len(environment_mappings() - before), 2)
        cache.close()
        self.assertEqual(len(environment_mappings() - before), 1)
        other.close()
        self.assertEqual(environment_mappings(), before)
        # Descriptor/loader-name reuse must not keep an old instance alive.
        for _ in range(40):
            self.assertEqual(cache.calculate_decomposition(*request), expected)
            self.assertEqual(len(environment_mappings() - before), 1)
            cache.close()
            self.assertEqual(environment_mappings(), before)

    def test_library_hides_internal_state_and_checks_environment_abi(self):
        library = ctypes.CDLL(str(Path(__file__).resolve().parents[1] / 'common/cpp/LiE/liblie.so'))
        self.assertEqual(library.n2_lie_environment_abi(), 1)
        for symbol in ('grp', 'defaultgrp', 'one', 'null', 'cmpfn', 'n2_lie_active',
                       'n2_lie_poll_budget', 'Adams', 'Tensor', 'n2_lie_allocate'):
            with self.subTest(symbol=symbol), self.assertRaises(AttributeError):
                getattr(library, symbol)

    @unittest.skipUnless(Path('/usr/bin/g++').is_file(), 'system g++ required')
    def test_standalone_environment_with_nonpie_executable(self):
        root = Path(__file__).resolve().parents[1]
        library = root / 'common/cpp/LiE'
        command = ['/usr/bin/g++', '-std=c++17', '-O2', '-pthread', '-fno-pie', '-no-pie',
                   '-Wall', '-Wextra', '-Wpedantic']
        prefix = Path(sys.prefix)
        if (prefix / 'include/gmpxx.h').is_file():
            command += [f'-I{prefix / "include"}', f'-L{prefix / "lib"}',
                        f'-Wl,-rpath,{prefix / "lib"}']
        command += [str(root / 'test/cpp/lie_wrapper_driver.cpp'), str(root / 'common/cpp/lie.cpp'),
                    f'-L{library}', f'-Wl,-rpath,{library}', '-llie', '-lgmpxx', '-lgmp', '-ldl',
                    '-o', str(self.directory / 'standalone')]
        build = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        run = subprocess.run([str(self.directory / 'standalone')], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('native wrapper checks passed', run.stdout)

    def test_32_live_environments_with_concurrent_deadlines_and_failures(self):
        before = environment_mappings()
        ready = threading.Barrier(33, timeout=20)
        release = threading.Event()
        def compute(worker):
            cache = native.CharacterDecompositionCache(
                str(self.directory / 'shared.db'), timeout=0.02 if worker % 4 == 0 else -1,
                max_objects=3 if worker % 4 == 1 else 9999999)
            try:
                if worker % 4 == 1:
                    with self.assertRaisesRegex(RuntimeError, 'object limit'):
                        cache.calculate_decomposition('A1', (1,), (0, 1))
                else:
                    cache.calculate_decomposition('A1', (1,), (0, 1))
                ready.wait()
                if not release.wait(20):
                    raise AssertionError('environment inspection did not finish')
                for _ in range(12):
                    if worker % 4 == 0:
                        with self.assertRaisesRegex(RuntimeError, 'timed out'):
                            cache.calculate_decomposition('E8', (1,) * 8, (0, 1))
                        # Recovery in the very same environment.
                        self.assertEqual(cache.calculate_decomposition('A1', (1,), (0, 1)),
                                         {(0,): -1, (2,): 1})
                    elif worker % 4 == 1:
                        with self.assertRaisesRegex(RuntimeError, 'object limit'):
                            cache.calculate_decomposition('A2', (1, 0), (0, 1))
                    elif worker % 4 == 2:
                        self.assertEqual(cache.get_decomposition('C2', (1, 0), (0, 1)),
                                         {(0, 0): -1, (0, 1): -1, (2, 0): 1})
                        self.assertEqual(cache.tensor_decompositions('A2', {(1, 0): 1}, {(0, 1): 1}),
                                         {(0, 0): 1, (1, 1): 1})
                    else:
                        huge = (1 << 20000) + worker
                        self.assertEqual(cache.tensor_decompositions('A1', {(1,): huge}, {(1,): -huge}),
                                         {(0,): -huge * huge, (2,): -huge * huge})
            finally:
                cache.close()
        with ThreadPoolExecutor(max_workers=32) as workers:
            futures = [workers.submit(compute, i) for i in range(32)]
            try:
                ready.wait()
                self.assertEqual(len(environment_mappings() - before), 32)
            finally:
                release.set()
            for future in futures:
                future.result(timeout=30)
        self.assertEqual(environment_mappings(), before)
        with sqlite3.connect(self.directory / 'shared.db') as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(), 'requires fork')
    def test_inherited_environment_and_unloaded_fork_handlers(self):
        cache = self.cache()
        for _ in range(20):
            cache.calculate_decomposition('A1', (1,), (0, 1))
            cache.close()
        cache.calculate_decomposition('A1', (1,), (0, 1))
        context = multiprocessing.get_context('fork')
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=inherited_cache_compute, args=(cache, child))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(15), 'inherited native environment hung')
            self.assertEqual(parent.recv(), {(0, 1): -1, (2, 0): 1})
            process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(cache.calculate_decomposition('A1', (1,), (0, 1)),
                             {(0,): -1, (2,): 1})
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()

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
