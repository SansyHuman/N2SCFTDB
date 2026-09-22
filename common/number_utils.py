"""Number conversion helpers."""

import re
from decimal import Decimal
from fractions import Fraction
from numbers import Rational
from operator import index
from typing import Any


EXACT_RATIONAL_TEXT_RE = re.compile(r"\+?\d+(?:/\d+)?\Z")


def as_nonnegative_fraction(value: Any, field_name: str) -> Fraction:
    """Accept exact rational numbers, finite Decimals, or integer/fraction text."""
    if isinstance(value, bool) or not isinstance(value, (Rational, Decimal, str)):
        raise ValueError(
            f"{field_name} must be an exact nonnegative rational"
        )

    if isinstance(value, str):
        compact = value.strip()
        if EXACT_RATIONAL_TEXT_RE.fullmatch(compact) is None:
            raise ValueError(
                f"{field_name} must be an exact nonnegative rational"
            )
        value = compact

    try:
        if isinstance(value, Rational):
            # Sage ZZ/QQ expose methods rather than Rational's properties.
            # Convert their exact components, never a rounded display string.
            numerator = value.numerator
            denominator = value.denominator
            if callable(numerator):
                numerator = numerator()
            if callable(denominator):
                denominator = denominator()
            result = Fraction(index(numerator), index(denominator))
        else:
            result = Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(
            f"{field_name} must be an exact nonnegative rational"
        ) from exc

    if result < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return result


def as_positive_fraction(value: Any, field_name: str) -> Fraction:
    """Return an exact positive rational supplied without a float."""
    result = as_nonnegative_fraction(value, field_name)
    if result == 0:
        raise ValueError(f"{field_name} must be positive")
    return result


def as_integer(value: Any, field_name: str) -> int:
    """Return an integer from an exact rational number or finite Decimal."""
    if isinstance(value, bool) or not isinstance(value, (Rational, Decimal)):
        raise ValueError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if result != value:
        raise ValueError(f"{field_name} must be an integer")
    return result


def as_nonnegative_int(value: Any, field_name: str) -> int:
    """Return an exact non-negative integer without accepting booleans or floats."""
    result = as_integer(value, field_name)
    if result < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")
    return result
