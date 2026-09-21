"""Search semantics against an in-memory relational fixture; no live MySQL."""

from contextlib import redirect_stdout
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
from io import StringIO
import json
import re
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from common import n2_theory_db as database
from gui import theory_search as search


SETTINGS = {
    "mysql/database": "search_test", "mysql/host": "db.invalid", "mysql/port": 3311,
    "mysql/user": "reader", "mysql/password": "private dummy",
    "mysql/unix_socket": "/tmp/search-test.sock", "mysql/connect_timeout": 17,
}


class ReadOnlyConnection:
    """Run the actual query, adapting only MySQL placeholders/JSON functions."""

    def __init__(self, sqlite):
        self.sqlite = sqlite
        self.closed = False
        self.queries = []

    def cursor(self, cursor_type):
        owner = self

        class Cursor:
            def __enter__(self):
                self.cursor = owner.sqlite.cursor()
                return self

            def __exit__(self, *_):
                self.cursor.close()

            def execute(self, sql, parameters):
                assert sql.startswith("SELECT "), "search must only read"
                owner.queries.append((sql, parameters))
                self.cursor.execute(sql.replace("%s", "?"),
                                    tuple(str(value) if isinstance(value, Decimal) else value
                                          for value in parameters))

            def fetchmany(self, size):
                # Small pages exercise streaming over multiple fetches.
                return [dict(row) for row in self.cursor.fetchmany(min(size, 2))]

        return Cursor()

    def close(self):
        self.closed = True


class TheorySearchTests(unittest.TestCase):
    def setUp(self):
        self.sqlite = sqlite3.connect(":memory:")
        self.addCleanup(self.sqlite.close)
        self.sqlite.row_factory = sqlite3.Row
        self.sqlite.create_function("JSON_TYPE", 1, lambda text: None if text is None else
                                    "STRING" if isinstance(json.loads(text), str) else "NULL")
        self.sqlite.create_function("JSON_UNQUOTE", 1, lambda text: None if text is None else
                                    json.loads(text) if isinstance(json.loads(text), str) else None)
        self.sqlite.create_function("regexp", 2, lambda pattern, text: bool(text and re.search(r"\S", text)))
        self.sqlite.create_function("JSON_CONTAINS", 2, self.json_contains)
        self.sqlite.create_collation("DECIMAL", lambda left, right:
                                     (Decimal(left) > Decimal(right)) - (Decimal(left) < Decimal(right)))
        self.sqlite.executescript("""
            CREATE TABLE theories(id INTEGER PRIMARY KEY);
            CREATE TABLE theory_properties(theory_id INTEGER PRIMARY KEY, central_charges_json TEXT,
                                           superconformal_index_json TEXT,
                                           central_charge_a_decimal TEXT COLLATE DECIMAL,
                                           central_charge_c_decimal TEXT COLLATE DECIMAL,
                                           superconformal_index_order INTEGER,
                                           disconnected_sector_count INTEGER);
            CREATE TABLE lagrangian_realizations(id INTEGER PRIMARY KEY, theory_id INTEGER);
            CREATE TABLE gauge_factors(lagrangian_realization_id INTEGER, cartan_type TEXT);
        """)
        specs = {
            1: ([('A1',)], Fraction(1, 3), Fraction(1, 2), '"1"', 18, 1),
            2: ([('A1', 'C2'), ('A2',)], Fraction(2, 3), Fraction(3, 4), None, 24, 2),
            3: ([('C2', 'A1')], Fraction(1), Fraction(2), '""', 24, 1),
            4: ([('A1', 'A1')], Fraction(1, 3) + Fraction(1, 10**40), Fraction(1, 2), '"1+t^4"', 24, 2),
            5: ([('A1', 'A1', 'C2')], None, None, 'null', 24, 0),
            6: ([], Fraction(1, 3), Fraction(1, 2), '"1"', None, 1),
            7: ([('A2',), ('A1',), ('C2',)], Fraction(1), Fraction(2), '[]', 24, 1),
            8: ([], None, None, None, None, None),
            9: ([('D4',)], Fraction(1, 8), Fraction(0), json.dumps('\t \n'), 24, None),
        }
        for theory_id, (realizations, a, c, index, order, sectors) in specs.items():
            self.sqlite.execute("INSERT INTO theories VALUES (?)", (theory_id,))
            if theory_id != 8:
                charges = None if a is None else json.dumps({
                    key: {"numerator": value.numerator, "denominator": value.denominator}
                    for key, value in (("a", a), ("c", c))})
                with localcontext() as context:
                    context.prec = 80
                    decimals = [None if value is None else str(
                        (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
                            Decimal('1e-30'), rounding=ROUND_HALF_UP)) for value in (a, c)]
                self.sqlite.execute("INSERT INTO theory_properties VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (theory_id, charges, index, *decimals, order, sectors))
            for offset, factors in enumerate(realizations, 1):
                realization_id = 100 * theory_id + offset
                self.sqlite.execute("INSERT INTO lagrangian_realizations VALUES (?, ?)", (realization_id, theory_id))
                self.sqlite.executemany("INSERT INTO gauge_factors VALUES (?, ?)",
                                        [(realization_id, factor) for factor in factors])

    @staticmethod
    def json_contains(document, candidate):
        if document is None:
            return None
        document, candidate = json.loads(document), json.loads(candidate)

        def contains(target, value):
            if isinstance(value, dict):
                return isinstance(target, dict) and all(
                    key in target and contains(target[key], child) for key, child in value.items())
            return type(target) is type(value) and target == value

        return contains(document, candidate)

    def find(self, **conditions):
        connection = ReadOnlyConnection(self.sqlite)
        found = []
        with patch.object(database, "connect_database", return_value=connection) as connect, \
             patch.object(database, "initialize_database", side_effect=AssertionError("unexpected schema initialization")):
            count = search.search_theories(SETTINGS, conditions, found.append, lambda _: None)
        self.assertEqual(count, len(found))
        self.assertTrue(connection.closed)
        connect.assert_called_once_with(
            "search_test", host="db.invalid", port=3311, user="reader", password="private dummy",
            unix_socket="/tmp/search-test.sock", connect_timeout=17, initialize_schema=False)
        self.assertEqual(len(connection.queries), 1)
        self.assertNotIn("input_json", connection.queries[0][0])
        self.assertEqual(connection.queries[0][0].split(" FROM ")[0], "SELECT t.id AS theory_id")
        return found

    def test_blank_conditions_include_theories_without_realizations_or_properties(self):
        self.assertEqual(self.find(), list(range(1, 10)))

    def test_gauge_multisets_quotes_and_alternatives(self):
        cases = {
            'a_1': [1, 2, 3, 4, 5, 7], '"A1"': [1, 7], 'A1, C2': [2, 3, 5],
            '"C2, A1"': [2, 3], 'A1, A1': [4, 5], '"A1, A1"': [4],
            '"A2"; "A1, A1"': [2, 4, 7], 'A1; "A1"; A1': [1, 2, 3, 4, 5, 7],
            'E8': [], 'd_4': [9],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.find(gauge_groups=text), expected)

    def test_theory_id_is_not_a_realization_id(self):
        self.assertEqual(self.find(theory_id="2"), [2])
        self.assertEqual(self.find(theory_id="201"), [])

    def test_exact_rationals_and_approximate_inclusive_bounds(self):
        self.assertEqual(self.find(a="1/3"), [1, 6])
        # Theory 4 has a different rational value but the same stored decimal.
        self.assertEqual(self.find(a_min="1/3", a_max="1/3"), [1, 4, 6])
        self.assertEqual(self.find(a_min="1/3", a_max=str(Fraction(1, 3) + Fraction(1, 10**40))), [1, 4, 6])
        self.assertEqual(self.find(a="0.125", c="0"), [9])
        self.assertEqual(self.find(a="2/16", c="0/3"), [9])
        self.assertEqual(self.find(a="2/6", c="0.5"), [1, 6])
        self.assertEqual(self.find(a="0.333333333333333333333333333333"), [])
        self.assertEqual(self.find(a="1.25e-1", c="-0.0"), [9])
        self.assertEqual(self.find(a_min="1"), [3, 7])
        self.assertEqual(self.find(c_max="0.5"), [1, 4, 6, 9])
        self.assertEqual(self.find(a="1/3", a_max="0.1"), [])

    def test_range_columns_and_exact_json_are_filtered_in_sql(self):
        sql, parameters = search.build_query(search.parse_conditions({
            'a': '0.125', 'c': '6/8', 'a_min': '1/16', 'a_max': '1/8', 'c_min': '.5', 'c_max': '1',
        }))
        self.assertIn('JSON_CONTAINS(p.central_charges_json, %s) = 1', sql)
        self.assertEqual(json.loads(parameters[0]), {'a': {'numerator': 1, 'denominator': 8},
                                                   'c': {'numerator': 3, 'denominator': 4}})
        for charge in ('a', 'c'):
            for operation in ('>=', '<='):
                self.assertIn(f'p.central_charge_{charge}_decimal {operation} %s', sql)
        self.assertEqual(parameters[1:], (Decimal('.0625'), Decimal('.125'), Decimal('.5'), Decimal('1')))
        self.assertTrue(all(isinstance(value, Decimal) for value in parameters[1:]))
        range_sql, _ = search.build_query(search.parse_conditions({'a_min': '1/3'}))
        self.assertNotIn('central_charges_json', range_sql)
        exact_sql, _ = search.build_query(search.parse_conditions({'a': '1/3'}))
        self.assertNotIn('central_charge_a_decimal', exact_sql)

    def test_missing_json_charges_do_not_match_exact_or_range_filters(self):
        self.sqlite.execute('UPDATE theory_properties SET central_charges_json = ? WHERE theory_id = 5', ('null',))
        self.assertNotIn(5, self.find(a='0'))
        self.assertNotIn(5, self.find(a_min='-1'))
        self.assertEqual(self.find(c_min='0', c_max='0'), [9])

    def test_minimum_full_index_order_is_inclusive_and_requires_present_known_index(self):
        # Stored cutoff, rather than the last nonzero monomial, determines order.
        for minimum, expected in ((0, list(range(1, 10))), (1, [1, 4]), (18, [1, 4]),
                                  (19, [4]), (24, [4]), (25, [])):
            with self.subTest(minimum=minimum):
                self.assertEqual(self.find(minimum_index_order=minimum), expected)
        self.sqlite.execute('UPDATE theory_properties SET superconformal_index_order = 0 WHERE theory_id = 6')
        self.assertEqual(self.find(minimum_index_order=1), [1, 4])
        self.assertEqual(self.find(minimum_index_order=0), list(range(1, 10)))

    def test_zero_order_removes_index_predicates_and_unnecessary_property_join(self):
        sql, parameters = search.build_query(search.parse_conditions({'minimum_index_order': 0}))
        self.assertNotIn('theory_properties', sql)
        self.assertNotIn('superconformal_index', sql)
        self.assertEqual(parameters, ())
        self.assertEqual(self.find(minimum_index_order=0, theory_id='8'), [8])

    def test_single_sector_filter_uses_stored_count_and_combines_with_other_filters(self):
        self.assertEqual(self.find(only_single_sector=True), [1, 3, 6, 7])
        self.assertEqual(self.find(only_single_sector=True, minimum_index_order=0), [1, 3, 6, 7])
        self.assertEqual(self.find(only_single_sector=True, minimum_index_order=18), [1])
        self.assertEqual(self.find(only_single_sector=True, minimum_index_order=24), [])
        self.assertEqual(self.find(only_single_sector=True, gauge_groups='A1, C2'), [3])
        self.assertEqual(self.find(gauge_groups='A1', a='1/3', c='0.5', minimum_index_order=18,
                                   only_single_sector=True), [1])
        self.assertEqual(self.find(gauge_groups='A1', theory_id='6'), [])

    def test_minimum_order_and_sector_count_are_bound_in_sql(self):
        sql, parameters = search.build_query(search.parse_conditions({
            'minimum_index_order': 2**64 - 1, 'only_single_sector': True}))
        self.assertIn('p.superconformal_index_order >= %s', sql)
        self.assertIn('p.disconnected_sector_count = %s', sql)
        self.assertEqual(parameters, (2**64 - 1, 1))

    def test_values_are_bound_parameters(self):
        sql, parameters = search.build_query(search.parse_conditions({'gauge_groups': '"A1, C2"; E8', 'theory_id': '123'}))
        self.assertNotIn('123', sql)
        self.assertNotIn('A1', sql)
        self.assertEqual(parameters, (123, 2, 'A1', 1, 'C2', 1, 'E8', 1))

    def test_invalid_conditions_never_connect(self):
        invalid = [
            {'gauge_groups': text} for text in ('A1;', ';A1', 'A1,,C2', '"A1', 'A1"', '""',
                                               'A1; ;C2', '"A1;C2"', 'D2', 'A0', 'A1 OR 1=1')
        ] + [{'a': text} for text in ('nan', 'inf', '1/0', '1+2', '1;DROP TABLE theories', 0.5)] + [
            {'theory_id': '0'}, {'theory_id': '-1'}, {'theory_id': '1.0'},
            {'theory_id': str(2**64)}, {'theory_id': '1 OR 1=1'}, {'a_min': '2', 'a_max': '1'},
        ] + [{'minimum_index_order': value} for value in (-1, 2**64, True, 1.0, '18', None, [])] + [
            {'only_single_sector': value} for value in ('true', 1, None)]
        for conditions in invalid:
            with self.subTest(conditions=conditions), patch.object(database, 'connect_database') as connect:
                with self.assertRaises(ValueError):
                    search.search_theories(SETTINGS, conditions, lambda _: None, lambda _: None)
                connect.assert_not_called()

    def test_worker_closes_before_completion_and_redacts_failures(self):
        for failure in (None, 'query', 'close'):
            with self.subTest(failure=failure):
                connection = MagicMock()
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchmany.return_value = []
                output = StringIO()
                if failure == 'query':
                    cursor.execute.side_effect = RuntimeError('private dummy query failed')

                def close():
                    self.assertNotIn('"complete"', output.getvalue())
                    if failure == 'close':
                        raise RuntimeError('private dummy close failed')

                connection.close.side_effect = close
                with patch.object(database, 'connect_database', return_value=connection), \
                     patch('sys.stdin', StringIO(json.dumps({'settings': SETTINGS, 'conditions': {}}))), \
                     redirect_stdout(output):
                    status = search.main()
                self.assertEqual(status, 0 if failure is None else 1)
                self.assertNotIn('private dummy', output.getvalue())
                self.assertEqual('"complete"' in output.getvalue(), failure is None)
                connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
