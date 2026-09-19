"""Search-worker checks using the existing job iterator, without real writes."""

from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import MagicMock, patch

from common import n2_theory_db as database
from common import n2_theory_properties as properties
from gui import index_search


SETTINGS = {
    "mysql/database": "index_search_test", "mysql/host": "db.invalid",
    "mysql/port": 3311, "mysql/user": "reader", "mysql/password": "test secret",
    "mysql/unix_socket": "/tmp/test-mysql.sock", "mysql/connect_timeout": 17,
    "index/full_max_order": 18, "index/coulomb_max_dimension": "9/2",
}


def row(theory_id, data, *, full=None, coulomb=None, spectrum=None, full_order=None, coulomb_max=None):
    result = {column: None for column in database._INDEX_COLUMNS.values()}
    result.update({
        "theory_id": theory_id, "realization_id": theory_id + 100,
        "input_json": json.dumps(data),
        "superconformal_index_json": full, "coulomb_branch_index_json": coulomb,
        "coulomb_branch_spectrum_json": spectrum,
        "superconformal_index_order": full_order,
        "coulomb_branch_index_max_dimension_json": json.dumps(coulomb_max),
    })
    return result


class IndexSearchTests(unittest.TestCase):
    def test_real_iterator_reads_all_pages_and_preserves_jobs_without_calculating(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [
            [row(1, {"algebra": "a_1", "hypermultiplets": []}),
             row(2, {"algebra": "A1", "hypermultiplets": []},
                 full='"1"', coulomb="null", spectrum="[]")],
            [row(3, {"gauge_groups": [{"id": "a", "algebra": "C2"},
                                      {"id": "b", "algebra": "A1"}],
                     "hypermultiplets": []}, full='"1"', coulomb='"1"')],
            [],
        ]
        jobs, groups = [], []
        with patch.object(database, "connect_database", return_value=connection) as connect, \
             patch.object(database, "initialize_database") as initialize, \
             patch.object(database, "update_lagrangian_indices") as update, \
             patch.object(properties, "calculate_n2_theory_indices") as calculate:
            count = index_search.search_index_jobs(
                SETTINGS, lambda group, job: (groups.append(group), jobs.append(job)), lambda _: None,
            )
        self.assertEqual(count, 3)
        self.assertEqual(groups[0], {"algebras": ["A1"], "label": "SU(2)"})
        self.assertEqual(groups[1], groups[0])
        self.assertEqual(groups[2], {"algebras": ["C2", "A1"], "label": "Sp(2) × SU(2)"})
        self.assertEqual(jobs[0]["input"]["algebra"], "a_1")
        self.assertEqual(jobs[0]["needed_fields"], [
            "superconformal_index", "coulomb_branch_index", "coulomb_branch_spectrum",
        ])
        self.assertEqual(jobs[1]["needed_fields"], ["coulomb_branch_index"])
        self.assertEqual(jobs[1]["unknown_precision"], ["superconformal_index"])
        self.assertEqual(jobs[2]["needed_fields"], ["coulomb_branch_spectrum"])
        self.assertEqual(jobs[2]["lagrangian_realization_id"], 103)
        self.assertEqual([call.args[1] for call in cursor.execute.call_args_list],
                         [(0, True, 100), (102, True, 100), (103, True, 100)])
        connect.assert_called_once_with(
            "index_search_test", host="db.invalid", port=3311, user="reader",
            password="test secret", unix_socket="/tmp/test-mysql.sock",
            connect_timeout=17, initialize_schema=False,
        )
        connection.close.assert_called_once()
        initialize.assert_not_called()
        update.assert_not_called()
        calculate.assert_not_called()

    def test_search_includes_only_missing_or_known_lower_precision_components(self):
        connection = MagicMock()
        data = {"algebra": "A1", "hypermultiplets": []}
        complete = dict(full='"1"', coulomb='"1"', spectrum='[]', full_order=18,
                        coulomb_max={"numerator": 9, "denominator": 2})
        cases = [
            {},  # equal cutoffs: already sufficient
            {"full_order": 17},
            {"coulomb_max": {"numerator": 449, "denominator": 100}},
            {"full_order": 20, "coulomb_max": {"numerator": 451, "denominator": 100}},
            {"full_order": None, "coulomb_max": None},  # unknown is not lower
            {"full_order": 17, "coulomb_max": None},
            {"spectrum": None},
            {"full": None},
            {"coulomb": 'null'},
        ]
        connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [
            [row(i, data, **{**complete, **values}) for i, values in enumerate(cases, 1)], [],
        ]
        jobs = []
        with patch.object(database, 'connect_database', return_value=connection):
            count = index_search.search_index_jobs(SETTINGS, lambda group, job: jobs.append(job), lambda _: None)
        self.assertEqual(count, 6)
        self.assertEqual([job['theory_id'] for job in jobs], [2, 3, 6, 7, 8, 9])
        self.assertEqual([job['needed_fields'] for job in jobs], [
            ['superconformal_index'], ['coulomb_branch_index'], ['superconformal_index'],
            ['coulomb_branch_spectrum'], ['superconformal_index'], ['coulomb_branch_index'],
        ])
        self.assertEqual(jobs[2]['unknown_precision'], ['coulomb_branch_index'])

    def test_invalid_cutoffs_are_rejected_before_connecting(self):
        for key, value in [('index/full_max_order', -1), ('index/full_max_order', 1.5),
                           ('index/coulomb_max_dimension', '1/0'), ('index/coulomb_max_dimension', '-1')]:
            with self.subTest(key=key, value=value), patch.object(database, 'connect_database') as connect:
                with self.assertRaises(ValueError):
                    index_search.search_index_jobs({**SETTINGS, key: value}, lambda *_: None, lambda _: None)
                connect.assert_not_called()

    def test_failure_closes_connection_and_does_not_publish_completion(self):
        for mode in ("query", "group", "close"):
            with self.subTest(mode=mode):
                connection = MagicMock()
                jobs = [{"theory_id": 1, "input": {"algebra": "invalid"},
                         "needed_fields": ["superconformal_index"]}] if mode == "group" else []
                kwargs = {"side_effect": RuntimeError("test secret query error")} if mode == "query" else {"return_value": iter(jobs)}
                if mode == "close":
                    connection.close.side_effect = RuntimeError("test secret close error")
                output = StringIO()
                with patch.object(database, "connect_database", return_value=connection), \
                     patch.object(database, "iter_lagrangian_index_jobs", **kwargs), \
                     patch("sys.stdin", StringIO(json.dumps({"settings": SETTINGS}) + "\n")), \
                     redirect_stdout(output):
                    status = index_search.main()
                self.assertEqual(status, 1)
                self.assertNotIn('"complete"', output.getvalue())
                self.assertNotIn("test secret", output.getvalue())
                self.assertIn('"level": "ERROR"', output.getvalue())
                connection.close.assert_called_once()

    def test_empty_search_completes_after_closing_connection(self):
        connection = MagicMock()
        output = StringIO()

        def check_close():
            self.assertNotIn('"complete"', output.getvalue())

        connection.close.side_effect = check_close
        with patch.object(database, "connect_database", return_value=connection), \
             patch.object(database, "iter_lagrangian_index_jobs", return_value=iter(())), \
             patch("sys.stdin", StringIO(json.dumps({"settings": SETTINGS}) + "\n")), \
             redirect_stdout(output):
            self.assertEqual(index_search.main(), 0)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), {"complete": 0})

    def test_missing_database_never_connects(self):
        settings = dict(SETTINGS, **{"mysql/database": " "})
        with patch.object(database, "connect_database") as connect, self.assertRaises(ValueError):
            index_search.search_index_jobs(settings, lambda *_: None, lambda _: None)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
