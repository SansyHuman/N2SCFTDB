"""Read-only theory search worker, launched with ``sage -python -m gui.theory_search``.

Search results are distinct theory IDs, not realization IDs. Large index/input
payloads stay in MySQL until a later export. SQL compares exact central-charge
JSON fractions and uses the indexed DECIMAL columns for approximate ranges.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import json
import re
import sys

from gui.logging_utils import make_log_record
from common.json_utils import json_text


@dataclass(frozen=True)
class GaugeCondition:
    factors: tuple[str, ...]
    exact: bool


@dataclass(frozen=True)
class SearchConditions:
    gauge_groups: tuple[GaugeCondition, ...]
    theory_id: int | None
    # Each charge has (exact value, inclusive lower bound, inclusive upper bound).
    a: tuple[Fraction | None, Fraction | None, Fraction | None]
    c: tuple[Fraction | None, Fraction | None, Fraction | None]
    minimum_index_order: int
    only_single_sector: bool

    @property
    def needs_charges(self):
        return any(value is not None for value in (*self.a, *self.c))


def parse_gauge_groups(text):
    """Parse semicolon alternatives; quoted groups require equal multisets."""
    if not isinstance(text, str):
        raise ValueError("Seed theories must be Cartan-type text.")
    if not text.strip():
        return ()
    from anomalies.lie_algebra import parse_cartan_type

    groups = []
    for expression in text.split(";"):
        expression = expression.strip()
        exact = expression.startswith('"') and expression.endswith('"')
        if exact:
            expression = expression[1:-1]
        if not expression.strip() or '"' in expression:
            raise ValueError('Use nonempty gauge groups separated by semicolons; quote the entire group, e.g. "A1, C2".')
        factors = []
        for factor in expression.split(","):
            family, rank = parse_cartan_type(factor.strip())
            factors.append(f"{family}{rank}")
        group = GaugeCondition(tuple(sorted(factors)), exact)
        if group not in groups:
            groups.append(group)
    return tuple(groups)


def _rational(text, label):
    if not isinstance(text, str):
        raise ValueError(f"{label} must be an integer, fraction or decimal entered as text.")
    if not text.strip():
        return None
    try:
        return Fraction(text.strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{label} must be an integer, fraction or finite decimal.") from exc


def parse_conditions(values):
    """Validate all user input before opening a database connection."""
    if not isinstance(values, dict):
        raise ValueError("Search conditions must be an object.")
    groups = parse_gauge_groups(values.get("gauge_groups", ""))
    text = values.get("theory_id", "")
    if not isinstance(text, str):
        raise ValueError("Theory id must be a positive integer.")
    theory_id = None
    if text.strip():
        if not re.fullmatch(r"\+?[0-9]+", text.strip()):
            raise ValueError("Theory id must be a positive integer.")
        theory_id = int(text)
        if not 1 <= theory_id <= 2**64 - 1:
            raise ValueError("Theory id must be between 1 and 18446744073709551615.")
    charges = {}
    for charge in ("a", "c"):
        exact, lower, upper = (
            _rational(values.get(key, ""), label)
            for key, label in ((charge, f"Central charge {charge}"),
                               (f"{charge}_min", f"Lower bound for {charge}"),
                               (f"{charge}_max", f"Upper bound for {charge}"))
        )
        if lower is not None and upper is not None and lower > upper:
            raise ValueError(f"The lower bound for {charge} must not exceed the upper bound.")
        charges[charge] = exact, lower, upper
    minimum_order = values.get("minimum_index_order", 0)
    if type(minimum_order) is not int or not 0 <= minimum_order <= 2**64 - 1:
        raise ValueError("Minimum full-index order must be an integer between 0 and 18446744073709551615.")
    only_single_sector = values.get("only_single_sector", False)
    if type(only_single_sector) is not bool:
        raise ValueError("Only theories with one disconnected sector must be true or false.")
    return SearchConditions(groups, theory_id, charges["a"], charges["c"],
                            minimum_order, only_single_sector)


def _decimal_bound(value):
    """Round a rational endpoint to the decimal columns' 30-place scale."""
    with localcontext() as context:
        context.prec = 80
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        context.prec = max(80, decimal.adjusted() + 31)
        return decimal.quantize(Decimal("1e-30"), rounding=ROUND_HALF_UP)


def build_query(conditions):
    """Use bound parameters for values and EXISTS to avoid duplicate theories.

    Every factor in an alternative must occur in the same realization. Counts
    preserve multiplicity (A1,A1 needs two factors); exact matches also require
    the total number of factors to agree. Factor order is immaterial.
    """
    statement = "SELECT t.id AS theory_id FROM theories AS t"
    if conditions.needs_charges or conditions.minimum_index_order > 0 or conditions.only_single_sector:
        statement += " JOIN theory_properties AS p ON p.theory_id = t.id"
    predicates, parameters = [], []
    if conditions.theory_id is not None:
        predicates.append("t.id = %s")
        parameters.append(conditions.theory_id)
    alternatives = []
    for group in conditions.gauge_groups:
        factors = []
        if group.exact:
            factors.append("(SELECT COUNT(*) FROM gauge_factors AS gf "
                           "WHERE gf.lagrangian_realization_id = lr.id) = %s")
            parameters.append(len(group.factors))
        for factor, multiplicity in sorted(Counter(group.factors).items()):
            factors.append("(SELECT COUNT(*) FROM gauge_factors AS gf "
                           "WHERE gf.lagrangian_realization_id = lr.id "
                           "AND gf.cartan_type = %s) >= %s")
            parameters.extend((factor, multiplicity))
        alternatives.append("(" + " AND ".join(factors) + ")")
    if alternatives:
        predicates.append("EXISTS (SELECT 1 FROM lagrangian_realizations AS lr "
                          "WHERE lr.theory_id = t.id AND (" + " OR ".join(alternatives) + "))")
    exact_charges = {charge: limits[0] for charge, limits in (("a", conditions.a), ("c", conditions.c))
                     if limits[0] is not None}
    if exact_charges:
        # Fraction reduces decimal/fraction input to the same canonical JSON
        # representation used when saving charges. Match both integer components
        # inside MySQL, without numeric casts or float conversion of the pair.
        predicates.append("JSON_CONTAINS(p.central_charges_json, %s) = 1")
        parameters.append(json_text(exact_charges, canonical=True))
    for charge, (_, lower, upper) in (("a", conditions.a), ("c", conditions.c)):
        # Keep indexed columns bare so MySQL can perform range scans.
        if lower is not None:
            predicates.append(f"p.central_charge_{charge}_decimal >= %s")
            parameters.append(_decimal_bound(lower))
        if upper is not None:
            predicates.append(f"p.central_charge_{charge}_decimal <= %s")
            parameters.append(_decimal_bound(upper))
    if conditions.minimum_index_order > 0:
        # Use the recorded inclusive cutoff, never the last nonzero term.
        # Unknown precision and absent/non-string indices cannot meet a positive
        # minimum. Zero adds no index restriction or property join of its own.
        predicates.append("JSON_TYPE(p.superconformal_index_json) = 'STRING' "
                          "AND JSON_UNQUOTE(p.superconformal_index_json) REGEXP '[^[:space:]]'")
        predicates.append("p.superconformal_index_order >= %s")
        parameters.append(conditions.minimum_index_order)
    if conditions.only_single_sector:
        predicates.append("p.disconnected_sector_count = %s")
        parameters.append(1)
    if predicates:
        statement += " WHERE " + " AND ".join(predicates)
    return statement + " ORDER BY t.id", tuple(parameters)


def search_theories(settings, values, emit_theory, log):
    """Stream matching IDs through a server-side cursor; never initialize or write."""
    conditions = parse_conditions(values)
    name = settings["mysql/database"].strip()
    if not name:
        raise ValueError("Set the MySQL database name in Settings → Preferences.")
    from common import n2_theory_db as database
    from pymysql.cursors import SSDictCursor

    statement, parameters = build_query(conditions)
    log("Searching theories with the supplied conditions…")
    connection = database.connect_database(
        name, host=settings["mysql/host"], port=settings["mysql/port"],
        user=settings["mysql/user"], password=settings["mysql/password"],
        unix_socket=settings.get("mysql/unix_socket"),
        connect_timeout=settings["mysql/connect_timeout"], initialize_schema=False,
    )
    count = 0
    try:
        # One SELECT gives one consistent statement snapshot without accumulating
        # full index strings or all candidate rows in memory.
        with connection.cursor(SSDictCursor) as cursor:
            cursor.execute(statement, parameters)
            while rows := cursor.fetchmany(256):
                for row in rows:
                    emit_theory(int(row["theory_id"]))
                    count += 1
                    if count % 1000 == 0:
                        log(f"Found {count} matching theories…")
    finally:
        connection.close()
    return count


def main():
    password = ""
    try:
        request = json.loads(sys.stdin.readline())
        settings = request["settings"]
        password = settings.get("mysql/password", "")

        def log(message):
            print(json.dumps(make_log_record(message, secrets=(password,))), flush=True)

        count = search_theories(
            settings, request.get("conditions", {}),
            lambda theory_id: print(json.dumps({"theory_id": theory_id}), flush=True), log,
        )
        # A complete result is only announced after closing the connection.
        print(json.dumps({"complete": count}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps(make_log_record(f"{type(exc).__name__}: {exc}", "ERROR",
                                         secrets=(password,))), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
