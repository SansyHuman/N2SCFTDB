"""Check reuse of persisted Adams products and the parallel dependency plan."""

from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from index.char_decomposition_cache import CharacterDecompositionCache as Cache
from test.test_character_decomposition_cache import su2_fundamental_adams_product


class CachedIdentityTests(unittest.TestCase):
    def test_first_adams_and_trivial_representation_need_no_lie(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.db'
            with Cache(database_path=path, lie_executable='must-not-run') as cache:
                self.assertEqual(cache.get_decomposition('A2', (1, 1), (1,)),
                                 {(1, 1): 1})
                with patch.object(cache, '_calculate_decomposition',
                                  wraps=cache._calculate_decomposition) as compute:
                    self.assertEqual(cache.get_decompositions([
                        ('A2', (0, 0), (0, 2, 0, 1)),
                    ]), [{(0, 0): 1}])
                    compute.assert_called_once()
            with Cache(database_path=path, lie_executable='must-not-run') as cache:
                self.assertEqual(cache.get_decomposition('A2', (1, 1), (1,)),
                                 {(1, 1): 1})


@unittest.skipUnless(shutil.which('lie'), 'LiE is required')
class CachedDecompositionPlanningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'cache.db'

    def cache(self, **kwargs):
        cache = Cache(database_path=self.path, max_workers=kwargs.pop('max_workers', 1),
                      **kwargs)
        self.addCleanup(cache.close)
        return cache

    def seed_composite_rows(self):
        with self.cache() as cache:
            for powers in [(0, 2), (0, 0, 2)]:
                cache.get_decomposition('A1', (1,), powers)
        # Keep psi_2(R)^2 and psi_3(R)^2, but remove their atomic prerequisites.
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                'DELETE FROM character_decompositions WHERE adams_order NOT IN (4, 6)'
            )

    def test_higher_atomic_adams_still_runs_once(self):
        cache = self.cache(lie_backend='subprocess')
        with patch.object(cache, '_run_lie', wraps=cache._run_lie) as run:
            self.assertEqual(cache.get_decomposition('A1', (1,), (0, 1)),
                             {(0,): -1, (2,): 1})
        run.assert_called_once_with(['Adams(2,[1],A1)'])

    def test_persisted_composite_blocks_need_no_atomic_dependencies(self):
        self.seed_composite_rows()
        cache = self.cache(lie_backend='subprocess')
        with patch.object(cache, '_run_lie', wraps=cache._run_lie) as run:
            result = cache.get_decomposition('A1', (1,), (0, 2, 2))
        self.assertEqual(result, su2_fundamental_adams_product((0, 2, 2)))
        self.assertEqual(run.call_count, 1)
        self.assertTrue(run.call_args.args[0][0].startswith('tensor('))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM character_decompositions'
            ).fetchone()[0], 3)

    def test_batch_planner_does_not_schedule_unused_atoms(self):
        self.seed_composite_rows()
        cache = self.cache()
        with patch.object(cache, '_calculate_decomposition',
                          wraps=cache._calculate_decomposition) as compute:
            result = cache.get_decompositions([('A1', (1,), (0, 2, 2))])
        self.assertEqual(result, [su2_fundamental_adams_product((0, 2, 2))])
        compute.assert_called_once()

    def test_cost_estimate_prefers_smaller_cached_pair(self):
        with self.cache() as cache:
            for powers in [(4,), (0, 2), (2, 1)]:
                cache.get_decomposition('A1', (1,), powers)
        cache = self.cache()
        block = cache.get_decomposition('A1', (1,), (2, 1))
        with patch.object(cache, '_tensor_decompositions',
                          wraps=cache._tensor_decompositions) as tensor:
            result = cache.get_decomposition('A1', (1,), (4, 2))
        self.assertEqual(result, su2_fundamental_adams_product((4, 2)))
        tensor.assert_called_once_with('A1', block, block)

    def test_parallel_cached_splits(self):
        with self.cache() as cache:
            for powers in [(2,), (0, 2), (0, 0, 2)]:
                cache.get_decomposition('A1', (1,), powers)
        requests = [('A1', (1,), p) for p in [(2, 2), (0, 2, 2), (2, 0, 2)]]
        expected = [su2_fundamental_adams_product(r[2]) for r in requests]
        cache = self.cache(max_workers=3)
        self.assertEqual(cache.get_decompositions(requests), expected)
        cache.close()
        with self.cache(lie_executable='must-not-run', max_workers=3) as reopened:
            self.assertEqual(reopened.get_decompositions(requests), expected)

    def test_external_writer_entries_are_visible_to_existing_client(self):
        reader = self.cache(lie_backend='subprocess')
        reader.get_decomposition('A1', (1,), (1,))
        with self.cache() as writer:
            for powers in [(0, 2), (0, 0, 2)]:
                writer.get_decomposition('A1', (1,), powers)
        with patch.object(reader, '_run_lie', wraps=reader._run_lie) as run:
            result = reader.get_decomposition('A1', (1,), (0, 2, 2))
        self.assertEqual(result, su2_fundamental_adams_product((0, 2, 2)))
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
