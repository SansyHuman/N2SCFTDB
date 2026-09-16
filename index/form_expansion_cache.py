"""SQLite-backed FORM cache for symbolic expansion of index."""

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from common.form_utils import run_form, split_signed_terms, split_top_level, validate_form_threads
from index.char_decomposition_cache import AdamsPowers


DEFAULT_FORM_CACHE_DATABASE = Path(__file__).resolve().parents[1] / "form_expansion_cache.db"


_RATIONAL_RE = re.compile(r"d\((-?\d+),(-?\d+)\)\Z")
_CHARACTER_RE = re.compile(r"C(\d+)\((\d+)\)(?:\^(\d+))?\Z")
_FUGACITY_RE = re.compile(r"([tyu])(?:\^(-?\d+))?\Z")
_INTEGER_RE = re.compile(r"\d+\Z")


@dataclass(frozen=True)
class IndexFormTerm:
    """One FORM monomial before gauge-singlet projection."""

    # Coefficient of the term
    coefficient: Fraction
    # Power of t
    t_power: int
    # Power of y fugacity
    y_power: int
    # Power of u fugacity
    u_power: int
    # products of characters with (character index, Adams powers of the character)
    characters: tuple[tuple[int, AdamsPowers], ...]


class FormExpansionCache:
    """Persist parsed expansions using the complete FORM program as the key.

    Programs are compared verbatim, including whitespace. Each thread/process
    opens its own connection, and FORM runs outside SQLite write transactions.
    Concurrent misses may calculate the same program, but only one row is kept.
    """

    def __init__(
        self,
        *,
        database_path: str | Path | None = None,
        form_executable: str = "form",
        tform_executable: str = "tform",
        form_threads: int = 1,
        timeout: float | None = 600,
    ) -> None:
        self.form_threads = validate_form_threads(form_threads)
        self.tform_executable = tform_executable
        if database_path is not None:
            path = Path(database_path)
        else:
            path = DEFAULT_FORM_CACHE_DATABASE
        self.database_path = path.resolve()
        self.form_executable = form_executable
        self.timeout = timeout
        self._local = threading.local()

    @staticmethod
    def _retry_busy(operation):
        for attempt in range(3):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xff
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def _connection(self) -> sqlite3.Connection:
        """Open the database lazily, without reusing inherited connections."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            if self._local.pid == os.getpid():
                return connection
            connection.close()
            self._local.connection = None

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                self._retry_busy(
                    lambda: connection.execute("PRAGMA journal_mode=WAL").fetchone()
                )
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS form_expansions (
                        program TEXT COLLATE BINARY NOT NULL PRIMARY KEY,
                        expansion_json TEXT NOT NULL
                    ) WITHOUT ROWID
                """)
        except BaseException:
            connection.close()
            raise
        self._local.connection = connection
        self._local.pid = os.getpid()
        return connection

    def close(self) -> None:
        """Close this thread's connection. A later lookup can reopen it."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def get_expansion(self, program: str) -> list[IndexFormTerm]:
        """Return the cached expansion, or run, parse and cache FORM on a miss."""
        if not isinstance(program, str):
            raise TypeError("FORM program must be a string")
        connection = self._connection()
        row = connection.execute(
            "SELECT expansion_json FROM form_expansions WHERE program=?",
            (program,),
        ).fetchone()
        if row is not None:
            return self._decode_expansion(row[0])

        output = run_form(
            program, form_executable=self.form_executable, timeout=self.timeout,
            tform_executable=self.tform_executable, form_threads=self.form_threads,
        )
        terms = self.parse_form_output(output)
        payload = self._encode_expansion(terms)

        def write():
            with connection:
                connection.execute("""
                    INSERT INTO form_expansions (program, expansion_json)
                    VALUES (?, ?) ON CONFLICT (program) DO NOTHING
                """, (program, payload))

        self._retry_busy(write)
        return terms

    @staticmethod
    def _encode_expansion(terms: list[IndexFormTerm]) -> str:
        """Serialize terms as JSON, retaining exact arbitrary-size fractions."""
        return json.dumps([
            [
                str(term.coefficient.numerator),
                str(term.coefficient.denominator),
                term.t_power,
                term.y_power,
                term.u_power,
                term.characters,
            ]
            for term in terms
        ], separators=(",", ":"))

    @staticmethod
    def _decode_expansion(payload: str) -> list[IndexFormTerm]:
        """Restore fractions and nested character tuples from JSON text."""
        return [
            IndexFormTerm(
                Fraction(int(numerator), int(denominator)),
                t_power,
                y_power,
                u_power,
                tuple((character, tuple(powers)) for character, powers in characters),
            )
            for numerator, denominator, t_power, y_power, u_power, characters
            in json.loads(payload)
        ]

    @staticmethod
    def _canonical_character_powers(raw: dict[int, int]) -> AdamsPowers:
        """Convert a mapping from Adams operation index to power to one formal character."""
        order = sum(adams * exponent for adams, exponent in raw.items())
        return tuple(raw.get(adams, 0) for adams in range(1, order + 1))

    @staticmethod
    def parse_form_output(output: str) -> list[IndexFormTerm]:
        """Parse FORM's exact flat polynomial without passing through floats."""
        marker = "result ="
        if marker not in output:
            raise RuntimeError(f"FORM output does not contain {marker!r}")
        expression = "".join(output.split(marker, 1)[1].split())
        if expression.endswith(";"):
            expression = expression[:-1]
        if not expression:
            raise RuntimeError("FORM returned an empty result")

        terms: list[IndexFormTerm] = []
        for raw_term in split_signed_terms(expression):
            sign = 1
            if raw_term.startswith("+"):
                raw_term = raw_term[1:]
            elif raw_term.startswith("-"):
                raw_term = raw_term[1:]
                sign = -1

            coefficient = Fraction(sign)
            powers = {"t": 0, "y": 0, "u": 0}
            characters: dict[int, dict[int, int]] = {}
            for factor in split_top_level(raw_term, "*"):
                if match := _RATIONAL_RE.fullmatch(factor):
                    coefficient *= Fraction(int(match.group(1)), int(match.group(2)))
                elif match := _CHARACTER_RE.fullmatch(factor):
                    character = int(match.group(1))
                    adams = int(match.group(2))
                    exponent = int(match.group(3) or 1)
                    character_powers = characters.setdefault(character, {})
                    character_powers[adams] = (
                        character_powers.get(adams, 0) + exponent
                    )
                elif match := _FUGACITY_RE.fullmatch(factor):
                    powers[match.group(1)] += int(match.group(2) or 1)
                elif _INTEGER_RE.fullmatch(factor):
                    coefficient *= int(factor)
                else:
                    raise RuntimeError(
                        f"could not parse FORM factor {factor!r} in {raw_term!r}"
                    )

            character_key = tuple(
                (character, FormExpansionCache._canonical_character_powers(character_powers))
                for character, character_powers in sorted(characters.items())
            )
            terms.append(
                IndexFormTerm(
                    coefficient,
                    powers["t"],
                    powers["y"],
                    powers["u"],
                    character_key,
                )
            )
        return terms
