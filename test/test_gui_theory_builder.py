"""Build orchestration checks with real Sage enumeration and isolated storage."""

import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from anomalies.check_n2_anomalies import check_input_data
from common import n2_theory_db as database
from common import n2_theory_iter as theories
from common import n2_theory_properties as properties
from gui.theory_builder import run_build, theory_representations
from index import char_decomposition_cache as cache


class TheoryBuilderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="n2-build-test-")
        self.addCleanup(directory.cleanup)
        self.cache_path = Path(directory.name) / "characters.db"
        self.settings = {
            "mysql/database": "isolated_test", "mysql/host": "db.invalid",
            "mysql/port": 3310, "mysql/user": "test_user", "mysql/password": "test password",
            "mysql/connect_timeout": 17, "index/full_max_order": 4,
            "cache/character_database": str(self.cache_path),
            "tools/lie_executable": "/usr/bin/lie", "tools/timeout": 31.0,
        }
        self.connection = MagicMock()
        self.connect = patch.object(database, "connect_database", return_value=self.connection).start()
        self.store = patch.object(database, "store_lagrangian_theory").start()
        self.addCleanup(patch.stopall)
        self.seen = set()

        def store(connection, candidate):
            key = json.dumps(candidate, sort_keys=True)
            inserted = key not in self.seen
            self.seen.add(key)
            return SimpleNamespace(inserted=inserted, theory_id=len(self.seen))

        self.store.side_effect = store
        self.logs = []

    def test_real_simple_and_product_enumeration_and_repeat_counts(self):
        with patch.object(theories, "enumerate_simple_theory_candidates",
                          wraps=theories.enumerate_simple_theory_candidates) as simple, \
             patch.object(theories, "enumerate_product_theory_candidates",
                          wraps=theories.enumerate_product_theory_candidates) as product, \
             patch.object(properties, "_calculate_superconformal_index") as index, \
             patch.object(cache, "build_decomposition_cache") as build:
            result = run_build(" A1 \n\n A1, A1 \nA1", self.settings, False, self.logs.append)
        self.assertEqual((simple.call_count, product.call_count), (2, 1))
        self.assertEqual(product.call_args.args, (("A1", "A1"),))
        self.assertEqual(result["candidates"], 12)
        self.assertEqual(result["valid"], 12)
        self.assertEqual(result["invalid"], 0)
        self.assertEqual(result["added"], 10)
        self.assertEqual(result["existing"], 2)
        self.assertEqual(result["errors"], 0)
        self.connect.assert_called_once_with(
            "isolated_test", host="db.invalid", port=3310, user="test_user",
            password="test password", connect_timeout=17,
        )
        self.connection.close.assert_called_once()
        index.assert_not_called()
        build.assert_not_called()
        self.assertTrue(any("A1, A1 summary" in line for line in self.logs))

    def test_invalid_checks_and_storage_errors_are_counted_separately(self):
        candidates = [
            {"algebra": "A1", "hypermultiplets": []},
            {"algebra": "A1", "hypermultiplets": [{"dynkin_labels": [1], "number": 1, "kind": "half"}]},
            {"algebra": "A1", "hypermultiplets": [{"dynkin_labels": [1], "number": -1}]},
            *theories.enumerate_simple_theory_candidates("A1"),
        ]
        self.store.side_effect = [RuntimeError("database unavailable"), SimpleNamespace(inserted=True, theory_id=9)]
        with patch.object(theories, "enumerate_simple_theory_candidates", return_value=candidates):
            result = run_build("A1", self.settings, False, self.logs.append)
        self.assertEqual((result["valid"], result["invalid"], result["added"], result["db_failed"]), (2, 3, 1, 1))
        self.assertEqual(result["status"], "completed with errors")
        self.assertIn("b0=4", "\n".join(self.logs))
        self.assertIn("Witten anomaly parity=1", "\n".join(self.logs))
        self.assertIn("database unavailable", "\n".join(self.logs))
        self.assertEqual(self.store.call_count, 2)

    def test_bad_groups_and_enumeration_failure_do_not_block_later_groups(self):
        original = theories.enumerate_simple_theory_candidates

        def enumerate_group(group):
            if group == "A2":
                raise RuntimeError("enumeration failed")
            return original(group)

        with patch.object(theories, "enumerate_simple_theory_candidates", side_effect=enumerate_group):
            result = run_build("A1,\nnot-a-group\nA2\nA1", self.settings, False, self.logs.append)
        self.assertEqual(result["valid"], 2)
        self.assertEqual(result["errors"], 3)
        self.assertIn("line 1", "\n".join(self.logs))
        self.assertIn("enumeration failed", "\n".join(self.logs))

    def test_cache_unions_all_valid_theories_and_passes_settings(self):
        self.settings["index/full_max_order"] = 19
        # Existing records and failed insertions still contribute valid representations.
        self.store.side_effect = lambda *_: SimpleNamespace(inserted=False, theory_id=1)
        with patch.object(cache, "build_decomposition_cache") as build:
            result = run_build("A2\nA1, A1\nA2", self.settings, True, self.logs.append)
        expected = set()
        for candidate in theories.enumerate_simple_theory_candidates("A2") + theories.enumerate_product_theory_candidates(["A1", "A1"]):
            expected.update(theory_representations(check_input_data(candidate)))
        actual = {call.args[:2] for call in build.call_args_list}
        self.assertEqual(actual, expected)
        self.assertEqual(build.call_count, len(expected))
        self.assertTrue({("A2", (1, 0)), ("A2", (0, 1)), ("A2", (1, 1)),
                         ("A1", (0,)), ("A1", (1,)), ("A1", (2,))} <= actual)
        for call in build.call_args_list:
            self.assertEqual(call.args[2], 9)
            self.assertEqual(call.kwargs["database_path"], self.cache_path)
            self.assertEqual(call.kwargs["lie_executable"], "/usr/bin/lie")
            self.assertEqual(call.kwargs["timeout"], 31.0)
        self.assertEqual(result["cache_built"], len(expected))

    def test_real_cache_build_and_warm_reuse(self):
        original = cache.build_decomposition_cache

        def serial(*args, **kwargs):
            return original(*args, **kwargs, processes=1)

        with patch.object(cache, "build_decomposition_cache", side_effect=serial):
            cold = run_build("A1", self.settings, True, self.logs.append)
            self.logs.clear()
            warm = run_build("A1", self.settings, True, self.logs.append)
        self.assertEqual(cold["errors"], 0)
        self.assertEqual(warm["errors"], 0)
        self.assertEqual(cold["cache_built"], 2)
        with sqlite3.connect(self.cache_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM character_decompositions").fetchone()[0], 6)
        progress = [line for line in self.logs if "computed=" in line]
        self.assertTrue(progress)
        self.assertTrue(all("computed=0," in line for line in progress))

    def test_cache_failure_preserves_counts_and_continues(self):
        self.store.side_effect = RuntimeError("insert failed")
        with patch.object(cache, "build_decomposition_cache", side_effect=[RuntimeError("LiE timed out"), {1: 1}]):
            result = run_build("A1", self.settings, True, self.logs.append)
        self.assertEqual((result["valid"], result["db_failed"], result["cache_built"]), (2, 2, 1))
        self.assertIn("LiE timed out", "\n".join(self.logs))

    def test_zero_adams_bound_and_stop_between_candidates(self):
        self.settings["index/full_max_order"] = 1
        with patch.object(cache, "build_decomposition_cache") as build:
            result = run_build("A1", self.settings, True, self.logs.append)
        self.assertEqual(result["valid"], 2)
        build.assert_not_called()
        self.assertIn("is zero", "\n".join(self.logs))
        self.store.reset_mock()
        result = run_build("A1", self.settings, False, self.logs.append,
                           cancelled=lambda: self.store.call_count == 1)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["valid"], 1)

    def test_missing_settings_connection_failure_and_check_errors(self):
        self.settings["mysql/database"] = ""
        result = run_build("A1", self.settings, False, self.logs.append)
        self.assertEqual(result["status"], "failed")
        self.connect.assert_not_called()
        self.settings["mysql/database"] = "isolated_test"
        self.connect.side_effect = RuntimeError("access denied")
        result = run_build("A1", self.settings, False, self.logs.append)
        self.assertEqual(result["status"], "failed")
        self.assertIn("access denied", "\n".join(self.logs))
        self.connect.side_effect = None
        with patch.object(properties, "calculate_n2_theory_properties", side_effect=RuntimeError("computation failed")):
            result = run_build("A1", self.settings, False, self.logs.append)
        self.assertEqual((result["check_failed"], result["invalid"]), (2, 0))


if __name__ == "__main__":
    unittest.main()
