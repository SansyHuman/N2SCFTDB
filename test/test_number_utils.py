from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from common.number_utils import (
    as_integer,
    as_nonnegative_fraction,
    as_nonnegative_int,
    as_positive_fraction,
)

try:
    from sage.all import QQ, RDF, RBF, RIF, RR, ZZ, RealField
except ImportError:
    SAGE_AVAILABLE = False
else:
    SAGE_AVAILABLE = True


CONVERTERS = (
    as_integer, as_nonnegative_int,
    as_nonnegative_fraction, as_positive_fraction,
)


class NumberUtilsTests(unittest.TestCase):
    def test_integer_inputs_remain_exact_python_ints(self):
        for value in (0, 2, -2, 10**100, Fraction(12, 3), Decimal("2.0")):
            with self.subTest(value=value):
                result = as_integer(value, "coefficient")
                self.assertIs(type(result), int)
                self.assertEqual(result, value)

    def test_rational_inputs_remain_exact_python_fractions(self):
        large = 10**100 + 1
        for value, expected in (
            (0, Fraction(0)),
            (large, Fraction(large)),
            (Fraction(large, 3), Fraction(large, 3)),
            (Decimal("1.25"), Fraction(5, 4)),
            ("  +12/10  ", Fraction(6, 5)),
            ("0/7", Fraction(0)),
            ("90", Fraction(90)),
        ):
            with self.subTest(value=value):
                result = as_nonnegative_fraction(value, "dimension")
                self.assertIs(type(result), Fraction)
                self.assertIs(type(result.numerator), int)
                self.assertIs(type(result.denominator), int)
                self.assertEqual(result, expected)

    def test_integer_converters_reject_nonintegral_numbers_and_text(self):
        for converter in (as_integer, as_nonnegative_int):
            for value in (Fraction(3, 2), Decimal("1.25"), "2", "2/1"):
                with self.subTest(converter=converter.__name__, value=value):
                    with self.assertRaisesRegex(ValueError, "coefficient"):
                        converter(value, "coefficient")

    def test_rational_text_syntax_stays_restricted(self):
        for value in (
            "", "1.25", "1e2", "1_000", "1 / 2", "-1", "1/-2",
            "1/0", "nan", "inf",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "dimension"):
                    as_nonnegative_fraction(value, "dimension")

    def test_rejects_booleans_floats_nonfinite_and_unsupported_inputs(self):
        for converter in CONVERTERS:
            for value in (
                True, False, 0.0, 2.0, 1.25, float("nan"), float("inf"),
                float("-inf"), Decimal("NaN"), Decimal("Infinity"),
                Decimal("-Infinity"), None, 2 + 0j, object(),
            ):
                with self.subTest(converter=converter.__name__, value=value):
                    with self.assertRaisesRegex(ValueError, "probe"):
                        converter(value, "probe")

    def test_numeric_looking_objects_are_not_coerced(self):
        class NumericLooking:
            def __str__(self):
                return "2"

            def __int__(self):
                return 2

            def __eq__(self, other):
                return other == 2

        for converter in CONVERTERS:
            with self.subTest(converter=converter.__name__):
                with self.assertRaisesRegex(ValueError, "probe"):
                    converter(NumericLooking(), "probe")

    def test_sign_and_zero_constraints(self):
        self.assertEqual(as_nonnegative_int(0, "count"), 0)
        self.assertEqual(as_nonnegative_fraction(0, "dimension"), Fraction(0))
        self.assertEqual(as_positive_fraction("1/3", "dimension"), Fraction(1, 3))
        for converter, value, message in (
            (as_nonnegative_int, -1, "count must be a nonnegative integer"),
            (as_nonnegative_fraction, Fraction(-1, 3), "count must be nonnegative"),
            (as_positive_fraction, 0, "count must be positive"),
        ):
            with self.subTest(converter=converter.__name__):
                with self.assertRaisesRegex(ValueError, message):
                    converter(value, "count")


@unittest.skipUnless(SAGE_AVAILABLE, "SageMath is not installed")
class SageNumberUtilsTests(unittest.TestCase):
    def test_sage_integers_and_rationals_remain_exact(self):
        large = 10**100 + 1
        for value in (ZZ(0), ZZ(-2), ZZ(large), QQ(2), QQ(-2)):
            with self.subTest(value=value):
                result = as_integer(value, "coefficient")
                self.assertIs(type(result), int)
                self.assertEqual(result, value)
        for value, expected in (
            (ZZ(0), Fraction(0)),
            (ZZ(large), Fraction(large)),
            (QQ(1) / 3, Fraction(1, 3)),
            (QQ(6) / 5, Fraction(6, 5)),
            (QQ(large) / 3, Fraction(large, 3)),
        ):
            with self.subTest(value=value):
                result = as_nonnegative_fraction(value, "dimension")
                self.assertIs(type(result), Fraction)
                self.assertIs(type(result.numerator), int)
                self.assertIs(type(result.denominator), int)
                self.assertEqual(result, expected)
        self.assertEqual(as_nonnegative_int(ZZ(2), "count"), 2)
        self.assertEqual(as_positive_fraction(QQ(6) / 5, "dimension"), Fraction(6, 5))
        with self.assertRaises(ValueError):
            as_integer(QQ(3) / 2, "coefficient")

    def test_rejects_sage_real_values_even_when_integral(self):
        for field in (RR, RealField(100), RDF, RIF, RBF):
            for value in (field(0), field(2), field("1.25"), field(1) / 3):
                for converter in CONVERTERS:
                    with self.subTest(
                        field=field, value=value, converter=converter.__name__
                    ):
                        with self.assertRaisesRegex(ValueError, "probe"):
                            converter(value, "probe")

    def test_rejects_sage_nonfinite_reals(self):
        for field in (RR, RealField(100), RDF):
            for value in (field("NaN"), field("+infinity"), field("-infinity")):
                for converter in CONVERTERS:
                    with self.subTest(
                        field=field, value=value, converter=converter.__name__
                    ):
                        with self.assertRaisesRegex(ValueError, "probe"):
                            converter(value, "probe")


if __name__ == "__main__":
    unittest.main()
