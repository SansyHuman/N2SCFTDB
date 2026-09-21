"""CSV fidelity, real spawned readers, progress and atomic publication checks."""

from concurrent.futures import ProcessPoolExecutor
import csv
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gui import theory_download as download


class FixtureCursor:
    """Execute the export SELECT against an isolated SQLite fixture."""

    def __init__(self, connection):
        self.cursor = connection.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params):
        self.cursor.execute(sql.replace('%s', '?'), params)

    def __iter__(self):
        for row in self.cursor:
            values = dict(row)
            for key in ('central_charge_a_decimal', 'central_charge_c_decimal'):
                if values.get(key) is not None:
                    values[key] = Decimal(values[key])
            yield values


class FixtureConnection:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.closed = False

    def cursor(self, *args):
        return FixtureCursor(self.connection)

    def close(self):
        self.connection.close()
        self.closed = True


def fixture_connect(settings):
    return FixtureConnection(settings['fixture_path'])


def initialize_spawned_fixture(settings, fields, stopped):
    # Importable initializer runs in actual spawned Python interpreters. The
    # export scheduler, streaming query, CSV writer and merger are production code.
    download._connect = fixture_connect
    download._initialize_worker(settings, fields, stopped)


def fixture_pool(**kwargs):
    kwargs['initializer'] = initialize_spawned_fixture
    return ProcessPoolExecutor(**kwargs)


class TheoryDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n2-csv-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'fixture.db'
        self.target = self.root / 'theories.csv'
        self.settings = {'mysql/database': 'fixture', 'tools/processes': 1,
                         'fixture_path': str(self.database)}
        self.ids = tuple(range(1, 98))
        self.records = []
        self.names = [field.name for field in download.CSV_FIELDS]
        self.sectors = [['left, "quoted"', 'Σ\nright'], []]
        self.sectors_json = json.dumps(self.sectors, ensure_ascii=False)
        self.connections = []
        self.addCleanup(download._close_connection)
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE theories (id INTEGER PRIMARY KEY, name TEXT)')
            db.execute('CREATE TABLE lagrangian_realizations '
                       '(id INTEGER PRIMARY KEY, theory_id INTEGER, gauge_group TEXT, input_json TEXT)')
            columns = ', '.join(f'{field.name} TEXT' for field in download.CSV_FIELDS
                                if field.expression.startswith('p.'))
            db.execute(f'CREATE TABLE theory_properties (theory_id INTEGER PRIMARY KEY, {columns})')
            db.executemany('INSERT INTO theories VALUES (?, ?)',
                           [(i, f'Theory {i}') for i in self.ids])
            db.execute('UPDATE theories SET name = ? WHERE id = 1', ('Σ, "quoted"\nnext line',))
            db.executemany('INSERT INTO lagrangian_realizations VALUES (?, ?, ?, ?)',
                           [(i, i, 'A1', '{"matter": [1, 2]}') for i in self.ids if i != 2])
            db.execute('INSERT INTO lagrangian_realizations VALUES (101, 1, ?, ?)',
                       ('A2, C2', '{"matter": "dual"}'))
            db.execute('INSERT INTO theory_properties '
                       '(theory_id, central_charge_a_decimal, superconformal_index_json, '
                       'disconnected_sector_count, disconnected_sectors_json) VALUES (1, ?, ?, ?, ?)',
                       ('0.333333333333333333333333333333', json.dumps('1 + t^2/u'), 2, self.sectors_json))

    def connect(self, settings):
        connection = fixture_connect(settings)
        self.connections.append(connection)
        return connection

    def run_download(self, ids=None, names=None, **kwargs):
        with patch.object(download, '_connect', self.connect):
            return download.download_theories(
                self.ids if ids is None else ids, self.names if names is None else names,
                self.settings, self.target, kwargs.pop('emit', self.records.append),
                lambda message: None, **kwargs)

    def read_csv(self):
        with self.target.open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    def assert_clean(self):
        self.assertEqual(list(self.root.glob('.n2-csv-*')), [])
        self.assertTrue(all(connection.closed for connection in self.connections))

    def test_all_fields_multiple_realizations_missing_data_and_exact_text(self):
        result = self.run_download(ids=(1, 2))
        rows = self.read_csv()
        self.assertEqual(list(rows[0]), self.names)
        self.assertEqual([row['theory_id'] for row in rows], ['1', '1', '2'])
        self.assertEqual([row['lagrangian_realization_id'] for row in rows], ['1', '101', ''])
        self.assertEqual(rows[0]['name'], 'Σ, "quoted"\nnext line')
        self.assertEqual(json.loads(rows[1]['input_json']), {'matter': 'dual'})
        self.assertEqual(rows[0]['central_charge_a_decimal'], '0.333333333333333333333333333333')
        self.assertEqual(json.loads(rows[0]['superconformal_index_json']), '1 + t^2/u')
        for row in rows[:2]:
            self.assertEqual(row['disconnected_sector_count'], '2')
            self.assertEqual(row['disconnected_sectors_json'], self.sectors_json)
            self.assertEqual(json.loads(row['disconnected_sectors_json']), self.sectors)
        self.assertTrue(all(rows[2][name] == '' for name in self.names[2:]))
        self.assertEqual(result['written'], 2)
        self.assertEqual(result['rows'], 3)
        self.assertEqual(self.records, [{'download_complete': result}])
        self.assertTrue(self.target.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assert_clean()

    def test_sector_fields_can_be_selected_without_other_properties(self):
        names = ['disconnected_sector_count', 'disconnected_sectors_json']
        self.run_download(ids=(1, 2), names=names)
        self.assertEqual(self.read_csv(), [dict(zip(names, ('2', self.sectors_json)))] * 2
                         + [dict.fromkeys(names, '')])
        self.assert_clean()

    def test_progress_counts_theories_and_publishes_only_complete_file(self):
        self.target.write_text('old CSV')

        def receive(record):
            if 'download_progress' in record:
                self.assertEqual(self.target.read_text(), 'old CSV')
            else:
                self.assertEqual(len(self.read_csv()), 98)
            self.records.append(record)

        result = self.run_download(names=['name', 'theory_id'], emit=receive)
        self.assertEqual([record['download_progress']['written'] for record in self.records[:-1]],
                         [32, 64, 96])
        self.assertEqual(result['written'], 97)
        self.assertEqual(result['rows'], 98)
        self.assertEqual(list(self.read_csv()[0]), ['name', 'theory_id'])
        self.assert_clean()

    def test_spawned_parallel_readers_produce_identical_csv(self):
        self.run_download()
        serial = self.target.read_bytes()
        self.settings['tools/processes'] = 4
        self.records.clear()
        with patch.object(download, 'ProcessPoolExecutor', fixture_pool):
            result = self.run_download()
        self.assertEqual(self.target.read_bytes(), serial)
        self.assertEqual(result['written'], 97)
        self.assertEqual([record['download_progress']['written'] for record in self.records[:-1]],
                         [32, 64, 96])
        self.assert_clean()

    def test_cancelled_download_preserves_existing_file_and_cleans_up(self):
        for workers in (1, 4):
            with self.subTest(workers=workers):
                self.settings['tools/processes'] = workers
                self.target.write_text('keep this file')
                self.records.clear()
                with patch.object(download, 'ProcessPoolExecutor', fixture_pool):
                    with self.assertRaises(download.DownloadCancelled):
                        self.run_download(cancelled=lambda: bool(self.records))
                self.assertEqual(self.target.read_text(), 'keep this file')
                self.assertEqual(len(self.records), 1)
                self.assert_clean()

    def test_deleted_theory_fails_instead_of_silently_omitting_it(self):
        for workers in (1, 4):
            with self.subTest(workers=workers):
                self.settings['tools/processes'] = workers
                self.target.write_text('keep this file')
                with patch.object(download, 'ProcessPoolExecutor', fixture_pool):
                    with self.assertRaisesRegex(RuntimeError, 'no longer exist'):
                        self.run_download(ids=(*self.ids, 1000))
                self.assertEqual(self.target.read_text(), 'keep this file')
                self.assert_clean()

    def test_query_and_write_failures_preserve_destination(self):
        failures = [(patch.object(FixtureCursor, 'execute', side_effect=RuntimeError('query failed')), RuntimeError),
                    (patch.object(download.os, 'fsync', side_effect=OSError('disk full')), OSError),
                    (patch.object(download.os, 'replace', side_effect=PermissionError('cannot replace')), PermissionError)]
        for failure, expected in failures:
            with self.subTest(expected=expected), failure:
                self.records.clear()
                self.target.write_text('old content')
                with self.assertRaises(expected):
                    self.run_download(ids=(1,))
                self.assertEqual(self.target.read_text(), 'old content')
                self.assertEqual(self.records, [])
                self.assert_clean()

    def test_invalid_requests_fail_before_creating_any_file(self):
        cases = [([], ['name'], {}), ([2, 1], ['name'], {}), ([1, 1], ['name'], {}),
                 ([True], ['name'], {}), ([0], ['name'], {}), ([2**64], ['name'], {}),
                 ([1], [], {}), ([1], ['name', 'name'], {}), ([1], ['name; DROP TABLE theories'], {}),
                 ([1], ['name'], {'mysql/database': ''}), ([1], ['name'], {'tools/processes': 0})]
        for ids, names, settings in cases:
            with self.subTest(ids=ids, names=names, settings=settings):
                with self.assertRaises(ValueError):
                    download.download_theories(ids, names, {**self.settings, **settings}, self.target,
                                               self.records.append, lambda message: None)
                self.assertFalse(self.target.exists())
                self.assert_clean()

    def test_worker_count_respects_cpu_limit_and_small_exports(self):
        for cpus, count, expected in [(32, 1000, 32), (8, 1000, 8), (2, 1000, 2), (1, 1000, 1),
                                      (32, 32, 1), (32, 33, 2), (-1, 1000, 32), (-1, 2048, 64)]:
            with self.subTest(cpus=cpus, count=count), patch.object(download.os, 'cpu_count', return_value=64):
                _, workers = download.validate_request(tuple(range(1, count + 1)), ['name'],
                                                       {**self.settings, 'tools/processes': cpus})
                self.assertEqual(workers, expected)


if __name__ == '__main__':
    unittest.main()
