"""Disconnected-sector storage using the first realization's input factor IDs."""

import json
import os
import unittest

from common import n2_theory_db as db
from common import n2_theory_properties as properties
from test.test_disconnected_index import SU2, SU3, BIFUNDAMENTAL, product_of


class SectorStorageTests(unittest.TestCase):
    def test_sector_ids_are_not_compared_as_shared_physical_properties(self):
        original = properties.calculate_n2_theory_properties(SU2)
        renamed = {**original, 'disconnected_sectors': (('different_factor_id',),)}
        self.assertEqual(db._shared_properties(original), db._shared_properties(renamed))


MYSQL_DATABASE = os.environ.get('N2_TEST_MYSQL_DATABASE')


@unittest.skipUnless(MYSQL_DATABASE, 'set N2_TEST_MYSQL_DATABASE for sector storage tests')
class SectorStorageMySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'test' not in MYSQL_DATABASE.lower():
            raise RuntimeError('Use a dedicated test database')
        cls.options = {'host': os.environ.get('N2_TEST_MYSQL_HOST', '127.0.0.1'),
                       'port': int(os.environ.get('N2_TEST_MYSQL_PORT', '3306')),
                       'user': os.environ.get('N2_TEST_MYSQL_USER', 'root'),
                       'password': os.environ.get('N2_TEST_MYSQL_PASSWORD', ''),
                       'unix_socket': os.environ.get('N2_TEST_MYSQL_UNIX_SOCKET')}
        cls.connection = db.connect_database(MYSQL_DATABASE, **cls.options)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def setUp(self):
        db._execute(self.connection, 'DELETE FROM theories')

    def row(self, theory_id):
        return db._fetchone(self.connection, 'SELECT * FROM theory_properties WHERE theory_id = %s', (theory_id,))

    def test_import_connected_disconnected_and_free_sectors(self):
        free = product_of(SU2, SU3)
        free['hypermultiplets'].append({'representations': {}, 'number': 3})
        cases = [(SU2, [['gauge']]), (BIFUNDAMENTAL, [['a', 'b']]),
                 (product_of(SU2, SU3), [['s0_gauge'], ['s1_gauge']]),
                 (free, [['s0_gauge'], ['s1_gauge'], []])]
        for data, expected in cases:
            with self.subTest(expected=expected):
                stored = db.store_lagrangian_theory(self.connection, data)
                row = self.row(stored.theory_id)
                self.assertEqual(row['disconnected_sector_count'], len(expected))
                self.assertEqual(json.loads(row['disconnected_sectors_json']), expected)
                combined = json.loads(row['properties_json'])
                self.assertEqual(combined['disconnected_sectors'], expected)
                self.assertEqual(combined['disconnected_sector_count'], len(expected))
                self.assertTrue(all(row[column] is None for column in db._INDEX_COLUMNS.values()))

    def test_first_realization_ids_survive_other_realizations_and_duplicate_aliases(self):
        data = {'gauge_groups': [{'id': 'first', 'algebra': 'A1'}],
                'hypermultiplets': [{'representations': {'first': 'fundamental'}, 'number': 4}]}
        stored = db.store_lagrangian_theory(self.connection, data)
        duplicate = db.store_lagrangian_theory(self.connection, SU2)
        self.assertFalse(duplicate.inserted)
        self.assertEqual(duplicate.theory_id, stored.theory_id)
        checked, values = db._checked_results(SU2)
        second = db._insert_realization(self.connection, stored.theory_id, 'e' * 64, SU2, checked, values)
        self.assertEqual(db._store_disconnected_sectors(self.connection, stored.theory_id, second, values), 0)
        self.assertEqual(json.loads(self.row(stored.theory_id)['disconnected_sectors_json']), [['first']])


if __name__ == '__main__':
    unittest.main()
