"""SQLite-backed LiE cache for products of Adams-operated characters.

For an irreducible representation ``R`` and a tuple ``powers`` whose entry
``powers[j - 1]`` is ``n_j``, this module caches the decomposition

    product_j Adams(j, R) ** n_j.

Decompositions and final singlet coefficients share a local SQLite database.
Keys use Cartan types, Dynkin labels and Adams powers, independently of FORM
character names. On singlet-cache misses, split each character product before
requesting decompositions and contract the two halves by character orthogonality:
if A = sum a_lambda chi_lambda and B = sum b_mu chi_mu, the singlet coefficient
of A*B is sum a_lambda b_(lambda dual). Only intermediate products are fully
decomposed; the complete product is never tensor-decomposed for this projection.

When the optional native extension is built, C++ supplies the SQLite cache,
exact character primitives and native LiE Adams/tensor operations. Python retains scheduling,
progress callbacks and overridable calculation hooks. Set
N2SCFTDB_CHAR_CACHE_BACKEND=python to use the reference implementation, or cpp
to require the extension; the default auto mode uses it when available.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager, ExitStack
from functools import lru_cache
import json
from multiprocessing import get_context
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.number_utils import as_integer, as_nonnegative_int
from index._char_cache_native import load_native


_NATIVE = load_native()


# Highest weight of the representation
DynkinLabels = tuple[int, ...]
# Character product number of each order of Adams operation
AdamsPowers = tuple[int, ...]
# Character decomposition of key representation and value coefficient
Decomposition = dict[DynkinLabels, int]
# Decomposition request of (cartan type, highest weight, Adams powers)
DecompositionRequest = tuple[str, DynkinLabels, AdamsPowers]
# Products of characters with dynkin labels and Adams powers
CharacterProduct = tuple[tuple[DynkinLabels, AdamsPowers], ...]


DEFAULT_CHAR_CACHE_DATABASE = Path(__file__).resolve().parents[1] / "char_decomposition_cache.db"
_SCHEMA_VERSION = 1
_CARTAN_TYPE_RE = re.compile(r"(?:[ABCD]\d+|E[678]|F4|G2)\Z")
_LIE_NOTICE_PREFIX = "New tree space with maximum number of nodes:"
_LIE_TERM_RE = re.compile(r"([+-]?\d+)X\[([0-9,]*)\]")
_PROCESS_CACHE = None


def _canonical_cartan_type(value: str) -> str:
    """Normalize the algebra name."""
    if not isinstance(value, str):
        raise ValueError("Cartan type must be a string")
    result = re.sub(r"[\s_-]+", "", value).upper()
    if _CARTAN_TYPE_RE.fullmatch(result) is None:
        raise ValueError(f"invalid LiE Cartan type {value!r}")
    return result


def _canonical_labels(labels: Iterable[int]) -> DynkinLabels:
    """Convert the input into a nonempty tuple of nonnegative integers."""
    try:
        result = tuple(as_nonnegative_int(value, "Dynkin label") for value in labels)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Dynkin labels must be nonnegative integers") from exc
    if not result or any(value < 0 for value in result):
        raise ValueError("Dynkin labels must be a nonempty tuple of nonnegative integers")
    return result


def _canonical_adams_powers(powers: Iterable[int]) -> AdamsPowers:
    """Produce a unique representation of Adams powers."""
    try:
        raw = tuple(as_nonnegative_int(value, "Adams power") for value in powers)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Adams powers must be nonnegative integers") from exc
    if any(value < 0 for value in raw):
        raise ValueError("Adams powers must be nonnegative integers")
    if _NATIVE is not None:
        return tuple(_NATIVE.canonical_adams_powers(raw))

    order = sum(position * value for position, value in enumerate(raw, start=1))
    if order <= 0:
        raise ValueError("at least one Adams power must be positive")
    if any(raw[position - 1] for position in range(order + 1, len(raw) + 1)):
        raise ValueError("an Adams index cannot exceed the total Adams order")
    return raw[:order] + (0,) * max(0, order - len(raw))


def _adams_order(powers: AdamsPowers) -> int:
    """Calculate the total Adams order."""
    return sum(position * value for position, value in enumerate(powers, start=1))


def _decomposition_dependencies(
    powers: AdamsPowers,
) -> tuple[AdamsPowers, ...]:
    """Return the two lower-factor products used by the recursive calculation."""
    if _NATIVE is not None:
        return tuple(tuple(part) for part in _NATIVE.decomposition_dependencies(powers))
    if sum(powers) <= 1:
        return ()
    split_position = next(
        position
        for position, value in enumerate(powers, start=1)
        if value
    )
    left_raw = list(powers)
    left_raw[split_position - 1] -= 1
    left = _canonical_adams_powers(left_raw)
    right_raw = [0] * split_position
    right_raw[-1] = 1
    return left, tuple(right_raw)


def _json_key(value) -> str:
    """Serialize a normalized key into compact JSON text."""
    return json.dumps(value, separators=(",", ":"))


def _canonical_request(
    cartan_type: str, labels: Iterable[int], powers: Iterable[int],
) -> DecompositionRequest:
    """Normalize the Cartan type, labels, and Adams powers."""
    algebra = _canonical_cartan_type(cartan_type)
    labels = _canonical_labels(labels)
    if len(labels) != int(algebra[1:]):
        raise ValueError("Dynkin-label length does not match Cartan rank")
    return algebra, labels, _canonical_adams_powers(powers)


def _canonical_product(algebra, product) -> CharacterProduct:
    """Merge repeated representations by adding their power vectors and sort factors."""
    if _NATIVE is not None:
        normalized = [_canonical_request(algebra, labels, powers)[1:]
                      for labels, powers in product]
        return tuple((tuple(labels), tuple(powers))
                     for labels, powers in _NATIVE.canonical_product(algebra, normalized))
    merged = {}
    for labels, powers in product:
        _, labels, powers = _canonical_request(algebra, labels, powers)
        previous = merged.setdefault(labels, [])
        previous.extend([0] * max(0, len(powers) - len(previous)))
        for position, value in enumerate(powers):
            previous[position] += value
    return tuple(sorted(
        ((labels, _canonical_adams_powers(powers)) for labels, powers in merged.items()),
        reverse=True,
    ))


@lru_cache(maxsize=None)
def _dual_permutation(algebra: str) -> tuple[int, ...]:
    """Compute the -w0 permutation once using the project's duality convention."""
    if _NATIVE is not None:
        return tuple(_NATIVE.dual_permutation(algebra))
    from anomalies.lie_algebra import conjugate_dynkin_labels, get_lie_algebra

    group = get_lie_algebra(algebra)
    result = []
    for position in range(group.rank):
        fundamental = tuple(int(i == position) for i in range(group.rank))
        dual = conjugate_dynkin_labels(group, fundamental)
        result.append(dual.index(1))
    return tuple(result)


def _singlet_pair(algebra: str, left: Decomposition, right: Decomposition) -> int:
    """Exact coefficient of 1 in A*B, including signed virtual characters."""
    if _NATIVE is not None:
        return _NATIVE.singlet_pair(algebra, _native_decomposition(left),
                                   _native_decomposition(right))
    if not left or not right:
        return 0
    if len(left) > len(right):
        left, right = right, left
    zero = (0,) * int(algebra[1:])
    if len(left) == 1 and zero in left:
        return left[zero] * right.get(zero, 0)
    permutation = _dual_permutation(algebra)
    return sum(coefficient * right.get(tuple(labels[i] for i in permutation), 0)
               for labels, coefficient in left.items())


def _split_character_product(algebra: str, product: CharacterProduct) -> tuple[CharacterProduct, CharacterProduct]:
    """Greedily split Adams factors using j * sum(labels) as a cost estimate.

    The estimate balances highest-weight sizes, not exact tensor complexity.
    An individual Adams operation stays intact; repeated factors may separate.
    """
    if _NATIVE is not None:
        return tuple(tuple((tuple(labels), tuple(powers)) for labels, powers in side)
                     for side in _NATIVE.split_character_product(algebra, product))
    atoms = [(labels, j, j * max(1, sum(labels)))
             for labels, powers in product
             for j, exponent in enumerate(powers, start=1)
             for _ in range(exponent)]
    atoms.sort(key=lambda item: (item[2], item[0], item[1]), reverse=True)
    sides = [[], []]
    weights = [0, 0]
    for labels, adams, weight in atoms:
        side = min(range(2), key=lambda i: (weights[i],
                   not any(previous == labels for previous, _ in sides[i]), i))
        sides[side].append((labels, (0,) * (adams - 1) + (1,)))
        weights[side] += weight
    return tuple(_canonical_product(algebra, side) for side in sides)


def _initialize_decomposition_worker(
    database_path: str,
    lie_executable: str,
    max_nodes: int,
    max_objects: int,
    timeout: float,
    lie_backend: str = "auto",
) -> None:
    global _PROCESS_CACHE
    _PROCESS_CACHE = CharacterDecompositionCache(
        database_path=database_path, lie_executable=lie_executable,
        max_nodes=max_nodes, max_objects=max_objects, timeout=timeout, max_workers=1,
        lie_backend=lie_backend,
    )


def _generate_decomposition_in_worker(request: DecompositionRequest) -> Decomposition:
    if _PROCESS_CACHE is None:
        raise RuntimeError("character-decomposition worker was not initialized")
    # Dependencies were committed by the parent after the preceding level.
    # Return the new value to the parent, which batches writes for this level.
    return _PROCESS_CACHE._calculate_decomposition(*request)


def _generate_decomposition_batch_in_worker(
    requests: Sequence[DecompositionRequest],
) -> list[tuple[DecompositionRequest, Decomposition]]:
    """Compute one batch; the parent persists it before advancing the order."""
    return [(request, _generate_decomposition_in_worker(request))
            for request in requests]


def _format_labels(labels: DynkinLabels) -> str:
    """Convert Dynkin labels to LiE syntax."""
    return "[" + ",".join(str(value) for value in labels) + "]"


def format_lie_decomposition(decomposition: Decomposition) -> str:
    """Convert a structured decomposition to a LiE polynomial."""
    if _NATIVE is not None:
        return _NATIVE.format_lie_decomposition(_native_decomposition(decomposition))
    if not decomposition:
        raise ValueError("the zero virtual representation has no LiE polynomial here")
    terms = []
    for labels, coefficient in sorted(decomposition.items()):
        if coefficient:
            terms.append(f"{coefficient:+d}X{_format_labels(labels)}")
    return "".join(terms).lstrip("+")


def parse_lie_decomposition(text: str, rank: int) -> Decomposition:
    """Parse one LiE decomposition polynomial into structured terms."""
    if _NATIVE is not None:
        return _NATIVE.parse_lie_decomposition(text, as_nonnegative_int(rank, "rank"))
    compact = "".join(text.split())
    while any(pair in compact for pair in ("++", "+-", "-+", "--")):
        compact = (
            compact.replace("++", "+")
            .replace("+-", "-")
            .replace("-+", "-")
            .replace("--", "+")
        )
    if compact == "0":
        return {}

    matches = list(_LIE_TERM_RE.finditer(compact))
    if not matches:
        raise RuntimeError(f"could not parse LiE decomposition: {text!r}")
    reconstructed = "".join(match.group(0) for match in matches)
    if reconstructed.lstrip("+") != compact.lstrip("+"):
        raise RuntimeError(f"partially parsed LiE decomposition: {text!r}")

    result: Decomposition = {}
    for match in matches:
        coefficient = int(match.group(1))
        labels = tuple(int(value) for value in match.group(2).split(","))
        if len(labels) != rank:
            raise RuntimeError(
                f"LiE returned {len(labels)} labels for a rank-{rank} algebra"
            )
        updated = result.get(labels, 0) + coefficient
        if updated:
            result[labels] = updated
        else:
            result.pop(labels, None)
    return result


def _native_decomposition(decomposition: Decomposition) -> Decomposition:
    """Apply the public exact-number rules before crossing the native boundary."""
    return {_canonical_labels(labels): as_integer(coefficient, "coefficient")
            for labels, coefficient in decomposition.items()}


class CharacterDecompositionCache:
    """Persist exact character decompositions and singlet coefficients in SQLite.

    With no path arguments, use ``char_decomposition_cache.db`` at the project
    root. ``cache_directory`` places that filename inside the supplied
    directory. ``database_path`` selects a file
    explicitly; these two arguments are mutually exclusive.

    Each thread opens its own connection and keeps its own in-memory cache.
    Workers open connections after process creation. SQLite transactions cover
    only completed cache writes, never LiE calculations. Coefficients are stored
    as decimal strings to preserve arbitrary-size signed integers.

    The optional C++ backend is also thread-local. Python owns the dependency
    scheduling and calculation hooks in either mode, including cache-building
    process pools and progress callbacks.
    """

    def __init__(
        self,
        cache_directory: str | Path | None = None,
        *,
        database_path: str | Path | None = None,
        lie_executable: str = "lie",
        max_nodes: int = 9_999_999,
        max_objects: int = 9_999_999,
        timeout: float = 600,
        max_workers: int | None = None,
        lie_backend: str = "auto",
    ) -> None:
        if cache_directory is not None and database_path is not None:
            raise ValueError("supply either cache_directory or database_path")
        if database_path is not None:
            path = Path(database_path)
        elif cache_directory is not None:
            path = Path(cache_directory) / DEFAULT_CHAR_CACHE_DATABASE.name
        else:
            path = DEFAULT_CHAR_CACHE_DATABASE
        self.database_path = path.resolve()
        self.lie_executable = lie_executable
        self.max_nodes = int(max_nodes)
        self.max_objects = int(max_objects)
        self.timeout = timeout
        if lie_backend not in {"auto", "native", "subprocess"}:
            raise ValueError("lie_backend must be auto, native, or subprocess")
        if lie_backend == "native" and _NATIVE is None:
            raise RuntimeError("native LiE requires the C++ character-cache extension")
        self._lie_backend = lie_backend
        if max_workers is not None and int(max_workers) <= 0:
            raise ValueError("max_workers must be positive or None")
        self.max_workers = None if max_workers is None else int(max_workers)
        self._local = threading.local()

    @property
    def backend(self) -> str:
        """Return the selected computational backend (cpp or python)."""
        return "cpp" if _NATIVE is not None else "python"

    @property
    def lie_backend(self) -> str:
        """Return the selected LiE engine; a custom executable selects subprocess."""
        if _NATIVE is None or self._lie_backend == "subprocess":
            return "subprocess"
        return ("native" if self._lie_backend == "native" or
                os.fspath(self.lie_executable) in ("lie", b"lie") else "subprocess")

    def _state(self):
        """Maintain each thread's connection, memory caches and pending writes."""
        state = getattr(self._local, "state", None)
        if state is None or state["pid"] != os.getpid():
            # Never use a connection inherited from a parent process.
            if state is not None and state["connection"] is not None:
                state["connection"].close()
            if state is not None and state["native"] is not None:
                state["native"].close()
            state = dict(pid=os.getpid(), connection=None, decompositions={},
                         singlets={}, partial_products={}, pending=None,
                         native=None, native_options=None)
            self._local.state = state
        return state

    def _native_cache(self):
        """Lazily create one native client per calling thread and process."""
        if _NATIVE is None:
            return None
        state = self._state()
        # The native API uses a negative deadline for an unlimited wait;
        # Python's subprocess treats a supplied negative timeout as expired.
        timeout = -1.0 if self.timeout is None else float(self.timeout)
        if self.timeout is not None and timeout < 0:
            timeout = 0.0
        options = (str(self.database_path), os.fspath(self.lie_executable),
                   self.max_nodes, self.max_objects, timeout, self._lie_backend)
        if state["native"] is None or state["native_options"] != options:
            if state["native"] is not None:
                state["native"].close()
            state["native"] = _NATIVE.CharacterDecompositionCache(*options)
            state["native_options"] = options
        return state["native"]

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
        """Open SQLite lazily and enable WAL."""
        state = self._state()
        if state["connection"] is not None:
            return state["connection"]
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if mode != "wal":
                self._retry_busy(lambda: connection.execute("PRAGMA journal_mode=WAL").fetchone())
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, _SCHEMA_VERSION):
                raise RuntimeError(f"unsupported character-cache schema version {version}")
            if version == 0:
                def initialize():
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        connection.execute("""
                            CREATE TABLE IF NOT EXISTS character_decompositions (
                                algebra TEXT NOT NULL,
                                dynkin_labels TEXT NOT NULL,
                                adams_powers TEXT NOT NULL,
                                adams_order INTEGER NOT NULL,
                                terms_json TEXT NOT NULL,
                                PRIMARY KEY (algebra, dynkin_labels, adams_powers)
                            ) WITHOUT ROWID
                        """)
                        connection.execute("""
                            CREATE TABLE IF NOT EXISTS singlet_coefficients (
                                algebra TEXT NOT NULL,
                                product_key TEXT NOT NULL,
                                coefficient TEXT NOT NULL,
                                PRIMARY KEY (algebra, product_key)
                            ) WITHOUT ROWID
                        """)
                        connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                self._retry_busy(initialize)
        except BaseException:
            connection.close()
            raise
        state["connection"] = connection
        return connection

    def close(self) -> None:
        """Close this thread's connection and release its in-memory entries."""
        state = self._state()
        if state["connection"] is not None:
            state["connection"].close()
        if state["native"] is not None:
            state["native"].close()
        self._local.state = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    @staticmethod
    def _encode_decomposition(decomposition):
        """Convert decomposition dictionary to JSON text."""
        if _NATIVE is not None:
            return _NATIVE.encode_decomposition(_native_decomposition(decomposition))
        return _json_key([
            [labels, str(as_integer(coefficient, "coefficient"))]
            for labels, coefficient in sorted(decomposition.items()) if coefficient
        ])

    @staticmethod
    def _decode_decomposition(payload):
        """Convert JSON text to decomposition dictionary."""
        if _NATIVE is not None:
            return _NATIVE.decode_decomposition(payload)
        return {tuple(labels): int(coefficient) for labels, coefficient in json.loads(payload)}

    def _write_decompositions(self, entries):
        """Write decompositions to database."""
        if _NATIVE is not None:
            entries = [(request, _native_decomposition(decomposition))
                       for request, decomposition in entries]
            if entries:
                self._retry_busy(lambda: self._native_cache().write_decompositions(entries))
            return
        rows = [
            (algebra, _json_key(labels), _json_key(powers), _adams_order(powers),
             self._encode_decomposition(decomposition))
            for (algebra, labels, powers), decomposition in entries
        ]
        if not rows:
            return
        connection = self._connection()
        def write():
            with connection:
                connection.executemany("""
                    INSERT INTO character_decompositions VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (algebra, dynkin_labels, adams_powers) DO NOTHING
                """, rows)
        self._retry_busy(write)

    def _lookup_decomposition(self, request):
        """Retrieve cached decomposition result."""
        memory = self._state()["decompositions"]
        if request in memory:
            return memory[request]
        if _NATIVE is not None:
            result = self._native_cache().lookup_decomposition(request)
            if result is not None:
                memory[request] = result
            return result
        algebra, labels, powers = request
        parameters = (algebra, _json_key(labels), _json_key(powers))
        sql = """SELECT terms_json FROM character_decompositions
                 WHERE algebra=? AND dynkin_labels=? AND adams_powers=?"""
        connection = self._connection()
        row = connection.execute(sql, parameters).fetchone()
        if row is None:
            return None
        decomposition = self._decode_decomposition(row[0])
        memory[request] = decomposition
        return decomposition

    def get_decomposition(
        self, cartan_type: str, labels: Iterable[int], powers: Iterable[int],
    ) -> Decomposition:
        """Return a copy of a persisted decomposition, calculating it on a miss."""
        request = _canonical_request(cartan_type, labels, powers)
        result = self._lookup_decomposition(request)
        if result is None:
            result = self._calculate_decomposition(*request)
            pending = self._state()["pending"]
            if pending is None:
                self._write_decompositions([(request, result)])
            else:
                pending.append((request, result))
                if len(pending) >= 64:
                    self._write_decompositions(pending)
                    pending.clear()
            self._state()["decompositions"][request] = result
        return dict(result)

    def _decomposition_dependencies(
        self, algebra: str, labels: DynkinLabels, powers: AdamsPowers,
    ) -> tuple[AdamsPowers, ...]:
        """Prefer two cached factors when the usual split needs new work.

        Keep the usual split when it already needs only one tensor product.
        Otherwise inspect keys for this same algebra/irrep, and rank available
        pairs by their numbers of irreducible terms. This estimates tensor
        work; it is not an exact prediction of LiE's running time.
        """
        usual = _decomposition_dependencies(powers)
        if not any(labels):
            return ()
        if sum(powers) <= 2:
            return usual

        def lookup(part):
            if part == (1,):
                return {labels: 1}
            return self._lookup_decomposition((algebra, labels, part))

        if all(lookup(part) is not None for part in usual):
            return usual

        rows = self._connection().execute(
            "SELECT adams_powers FROM character_decompositions "
            "WHERE algebra=? AND dynkin_labels=? AND adams_order<?",
            (algebra, _json_key(labels), len(powers)),
        )
        available = {tuple(json.loads(row[0])) for row in rows}
        available.update(
            part for (group, weight, part) in self._state()["decompositions"]
            if group == algebra and weight == labels and len(part) < len(powers)
        )
        available.add((1,))
        best = None
        for left in sorted(available):
            remainder = list(powers)
            for position, value in enumerate(left):
                remainder[position] -= value
            if any(value < 0 for value in remainder):
                continue
            right = _canonical_adams_powers(remainder)
            if right not in available or left > right:
                continue
            a, b = lookup(left), lookup(right)
            if a is None or b is None:
                continue
            # A zero or scalar factor is handled without LiE by the tensor helper.
            zero = (0,) * len(labels)
            scalar = (len(a) == 1 and zero in a) or (len(b) == 1 and zero in b)
            cost = 0 if not a or not b or scalar else len(a) * len(b)
            score = (cost, len(a) + len(b), left, right)
            if best is None or score < best:
                best = score
        return usual if best is None else best[-2:]

    @contextmanager
    def _batch_decompositions(self):
        """Buffer completed results without holding a transaction during LiE."""
        state = self._state()
        if state["pending"] is not None:
            yield
            return
        state["pending"] = []
        try:
            yield
        finally:
            pending = state["pending"]
            state["pending"] = None
            self._write_decompositions(pending)

    def get_decompositions(
        self, requests: Sequence[tuple[str, Iterable[int], Iterable[int]]],
    ) -> list[Decomposition]:
        """Generate missing prerequisites by level, reusing persisted results."""
        requests = [_canonical_request(*request) for request in requests]
        required = set()
        pending = list(set(requests))
        by_level = {}
        while pending:
            request = pending.pop()
            if request in required:
                continue
            required.add(request)
            if self._lookup_decomposition(request) is not None:
                continue
            algebra, labels, powers = request
            by_level.setdefault(sum(powers), []).append(request)
            pending.extend(
                (algebra, labels, dependency)
                for dependency in self._decomposition_dependencies(algebra, labels, powers)
            )
        if by_level:
            workers = min(self.max_workers or (os.cpu_count() or 1),
                          max(map(len, by_level.values())))
            if workers == 1:
                with self._batch_decompositions():
                    for level in sorted(by_level):
                        for request in sorted(by_level[level]):
                            self.get_decomposition(*request)
            else:
                # Do not fork this client's live SQLite connection.
                self.close()
                with ProcessPoolExecutor(
                    max_workers=workers, initializer=_initialize_decomposition_worker,
                    initargs=(str(self.database_path), self.lie_executable,
                              self.max_nodes, self.max_objects, self.timeout, self._lie_backend),
                ) as executor:
                    for level in sorted(by_level):
                        level_requests = sorted(by_level[level])
                        results = executor.map(_generate_decomposition_in_worker, level_requests)
                        entries = list(zip(level_requests, results, strict=True))
                        self._write_decompositions(entries)
                        self._state()["decompositions"].update(entries)
        return [self.get_decomposition(*request) for request in requests]

    def _lookup_singlets(self, algebra, keys):
        """Retrieve cached singlet coefficients."""
        memory = self._state()["singlets"]
        if _NATIVE is not None:
            unique = list(dict.fromkeys(keys))
            result = {key: memory[algebra, key] for key in unique if (algebra, key) in memory}
            missing = [key for key in unique if key not in result]
            if missing or not unique:
                found = self._native_cache().lookup_singlets(algebra, missing)
                memory.update(((algebra, key), value) for key, value in found.items())
                result.update(found)
            return result
        connection = self._connection()
        result = {}
        for key in dict.fromkeys(keys):
            if (algebra, key) in memory:
                result[key] = memory[algebra, key]
                continue
            row = connection.execute(
                "SELECT coefficient FROM singlet_coefficients WHERE algebra=? AND product_key=?",
                (algebra, key),
            ).fetchone()
            if row is not None:
                result[key] = memory[algebra, key] = int(row[0])
        return result

    def _write_singlets(self, algebra, entries):
        """Insert singlet coefficients into database."""
        if not entries:
            return
        if _NATIVE is not None:
            values = {key: as_integer(value, "coefficient") for key, value in entries.items()}
            self._retry_busy(lambda: self._native_cache().write_singlets(algebra, values))
            self._state()["singlets"].update(((algebra, key), value) for key, value in values.items())
            return
        connection = self._connection()
        def write():
            with connection:
                connection.executemany("""
                    INSERT INTO singlet_coefficients VALUES (?, ?, ?)
                    ON CONFLICT (algebra, product_key) DO NOTHING
                """, [(algebra, key, str(value)) for key, value in entries.items()])
        self._retry_busy(write)
        self._state()["singlets"].update(((algebra, key), value) for key, value in entries.items())

    def get_singlet_multiplicities(
        self,
        cartan_type: str,
        rank: int,
        products: Sequence[Sequence[tuple[Iterable[int], Iterable[int]]]],
    ) -> list[int]:
        """Look up character-product singlets before loading any decomposition.

        Each product contains ``(Dynkin labels, Adams powers)`` pairs for one
        simple factor. Repeated irreps are merged and factors sorted; conjugate
        irreps retain their actual orientation. Cached zeros are valid hits.
        Misses use two partial decompositions and an exact dual-irrep pairing.
        """
        algebra = _canonical_cartan_type(cartan_type)
        if as_nonnegative_int(rank, "rank") != int(algebra[1:]):
            raise ValueError("rank does not match Cartan type")
        products = [_canonical_product(algebra, product) for product in products]
        keys = [_json_key(["characters", product]) for product in products]
        found = self._lookup_singlets(algebra, keys)
        missing = {key: product for key, product in zip(keys, products) if key not in found}
        if missing:
            values = self._character_singlets(algebra, rank, list(missing.values()))
            entries = dict(zip(missing, values, strict=True))
            self._write_singlets(algebra, entries)
            found.update(entries)
        return [found[key] for key in keys]

    def singlet_multiplicities(
        self,
        cartan_type: str,
        rank: int,
        products: Sequence[Sequence[Decomposition]],
    ) -> list[int]:
        """Cache singlets for callers supplying already decomposed characters."""
        algebra = _canonical_cartan_type(cartan_type)
        if as_nonnegative_int(rank, "rank") != int(algebra[1:]):
            raise ValueError("rank does not match Cartan type")
        products = [list(product) for product in products]
        keys = [_json_key(["decompositions", sorted(
            self._encode_decomposition(decomposition) for decomposition in product
        )]) for product in products]
        found = self._lookup_singlets(algebra, keys)
        missing = {key: product for key, product in zip(keys, products) if key not in found}
        if missing:
            values = self._compute_singlet_multiplicities(algebra, rank, list(missing.values()))
            entries = dict(zip(missing, values, strict=True))
            self._write_singlets(algebra, entries)
            found.update(entries)
        return [found[key] for key in keys]

    def _tensor_decompositions(
        self, algebra: str, left: Decomposition, right: Decomposition,
    ) -> Decomposition:
        """Decompose an intermediate product; never used for the final pairing."""
        if not left or not right:
            return {}
        zero = (0,) * int(algebra[1:])
        if set(left) == {zero}:
            return {labels: left[zero] * value for labels, value in right.items()
                    if left[zero] * value}
        if set(right) == {zero}:
            return {labels: right[zero] * value for labels, value in left.items()
                    if right[zero] * value}
        if self.lie_backend == "native":
            return self._native_cache().tensor_decompositions(
                algebra, _native_decomposition(left), _native_decomposition(right))
        expression = f"tensor({format_lie_decomposition(left)},{format_lie_decomposition(right)},{algebra})"
        output = self._run_lie([expression])
        return parse_lie_decomposition("".join(output), len(zero))

    def _character_singlets(
        self, algebra: str, rank: int, products: Sequence[CharacterProduct],
    ) -> list[int]:
        """Split monomials before decomposition and pair their two halves.

        Same-irrep partial products reuse the persistent decomposition cache.
        Mixed partial products are memoized in this client's thread-local state.
        Only the final scalar is persisted for the complete character product.
        """
        identity = {(0,) * rank: 1}
        plans = {}
        pure = set()
        splits = []

        def plan(product):
            if not product or product in plans:
                return
            if len(product) == 1:
                labels, powers = product[0]
                pure.add((algebra, labels, powers))
                plans[product] = None
                return
            left, right = _split_character_product(algebra, product)
            plans[product] = left, right
            plan(left)
            plan(right)

        for product in products:
            # Adams operations preserve the trivial character.
            product = tuple((labels, powers) for labels, powers in product if any(labels))
            if sum(sum(powers) for _, powers in product) <= 1:
                left, right = product, ()
            else:
                left, right = _split_character_product(algebra, product)
            splits.append((left, right))
            plan(left)
            plan(right)

        requests = sorted(pure)
        decompositions = dict(zip(requests, self.get_decompositions(requests), strict=True))
        memory = self._state()["partial_products"]

        def expand(product):
            if not product:
                return identity
            if len(product) == 1:
                labels, powers = product[0]
                return decompositions[algebra, labels, powers]
            key = algebra, product
            if key not in memory:
                left, right = plans[product]
                memory[key] = self._tensor_decompositions(algebra, expand(left), expand(right))
            return memory[key]

        return [_singlet_pair(algebra, expand(left), expand(right)) for left, right in splits]

    def _compute_singlet_multiplicities(
        self, cartan_type: str, rank: int,
        products: Sequence[Sequence[Decomposition]],
    ) -> list[int]:
        """Contract two partial decompositions using character orthogonality."""
        algebra = _canonical_cartan_type(cartan_type)
        identity = {(0,) * rank: 1}
        memo = {}

        def expand(factors):
            if not factors:
                return identity
            if len(factors) == 1:
                return factors[0]
            key = tuple(sorted(self._encode_decomposition(factor) for factor in factors))
            if key not in memo:
                middle = len(factors) // 2
                memo[key] = self._tensor_decompositions(
                    algebra, expand(factors[:middle]), expand(factors[middle:]))
            return memo[key]

        results = []
        for product in products:
            factors = [{labels: value for labels, value in factor.items() if value}
                       for factor in product]
            if any(not factor for factor in factors):
                results.append(0)
                continue
            if not factors:
                results.append(1)
                continue
            if len(factors) == 1:
                results.append(factors[0].get((0,) * rank, 0))
                continue
            factors.sort(key=lambda factor: (len(factor), self._encode_decomposition(factor)))
            middle = len(factors) // 2
            results.append(_singlet_pair(algebra, expand(factors[:middle]), expand(factors[middle:])))
        return results

    def _calculate_decomposition(
        self,
        algebra: str,
        labels: DynkinLabels,
        powers: AdamsPowers,
    ) -> Decomposition:
        """Calculate decomposition of given Adams powers."""
        if powers == (1,) or not any(labels):
            return {labels: 1}
        level = sum(powers)
        if level == 1:
            adams = next(
                position
                for position, value in enumerate(powers, start=1)
                if value
            )
            if self.lie_backend == "native":
                return self._native_cache().calculate_decomposition(algebra, labels, powers)
            expression = f"Adams({adams},{_format_labels(labels)},{algebra})"
        else:
            left, right = self._decomposition_dependencies(algebra, labels, powers)
            left_decomposition = self.get_decomposition(algebra, labels, left)
            right_decomposition = self.get_decomposition(algebra, labels, right)
            return self._tensor_decompositions(
                algebra, left_decomposition, right_decomposition,
            )

        output = self._run_lie([expression])
        if not output:
            raise RuntimeError(f"LiE returned no result for {expression!r}")
        return parse_lie_decomposition("".join(output), len(labels))

    def _run_lie(self, expressions: Sequence[str]) -> list[str]:
        """Execute LiE on given expressions."""
        result = self._execute_lie(expressions, configure_memory=False)
        lines = self._lie_output_lines(result.stdout)
        needs_retry = (
            result.returncode != 0
            or bool(result.stderr.strip())
            or any("(" in line or "error" in line.lower() for line in lines)
        )
        if needs_retry:
            result = self._execute_lie(expressions, configure_memory=True)
            lines = self._lie_output_lines(result.stdout)

        if result.returncode != 0 or result.stderr.strip():
            raise RuntimeError(
                f"LiE failed with code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return lines

    def _execute_lie(
        self,
        expressions: Sequence[str],
        *,
        configure_memory: bool,
    ) -> subprocess.CompletedProcess[str]:
        """Send LiE program through standard input and capture output."""
        if _NATIVE is not None:
            result = self._native_cache().execute_lie(list(expressions), configure_memory)
            return subprocess.CompletedProcess([self.lie_executable], result.returncode,
                                               result.stdout_text, result.stderr_text)
        commands = list(expressions)
        if configure_memory:
            commands[:0] = [
                f"maxnodes {self.max_nodes}",
                f"maxobjects {self.max_objects}",
            ]
        code = "\n".join(commands)
        try:
            return subprocess.run(
                [self.lie_executable],
                input=code,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"LiE executable {self.lie_executable!r} was not found"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("LiE calculation timed out") from exc

    @staticmethod
    def _lie_output_lines(stdout: str) -> list[str]:
        """Remove empty lines and LiE's informational messages."""
        if _NATIVE is not None:
            return _NATIVE.lie_output_lines(stdout)
        return [
            line.strip()
            for line in stdout.splitlines()
            if line.strip() and not line.startswith(_LIE_NOTICE_PREFIX)
        ]


def build_decomposition_cache(
    cartan_type: str,
    dynkin_labels: Iterable[int],
    max_adams_order: int,
    *,
    cache_directory: str | Path | None = None,
    database_path: str | Path | None = None,
    processes: int | None = None,
    lie_executable: str = "lie",
    lie_backend: str = "auto",
    timeout: float | None = 600,
    progress: Callable[[int, int, int], None] | None = None,
) -> dict[int, int]:
    """Persist every Adams-character product through ``max_adams_order``.

    At order n, ``frobenius_solve(range(1, n + 1), n)`` enumerates every
    nonnegative vector (a_1, ..., a_n) with sum(j*a_j) = n. Lower orders are
    committed first, so a composite product needs just one tensor operation
    on cached factors. Only a missing individual psi_n(R), n > 1, needs Adams;
    psi_1(R) and Adams operations on the trivial representation are known.

    Existing rows are skipped. Return {order: total number of products},
    including reused rows. The optional parent-process callback receives
    (order, total, computed) after the order has been committed. Completed
    batches remain usable if a later calculation fails or the build stops.

    A persistent, lazily started process pool divides each order into batches.
    Use processes=1 to run serially; the default uses the available CPU count.
    Scripts using multiple processes should call this under a __main__ guard.
    Cache path options follow CharacterDecompositionCache's conventions.
    """
    algebra, labels, _ = _canonical_request(cartan_type, dynkin_labels, (1,))
    maximum = as_nonnegative_int(max_adams_order, "maximum Adams order")
    if maximum == 0:
        raise ValueError("maximum Adams order must be positive")
    workers = (os.cpu_count() or 1) if processes is None else as_nonnegative_int(
        processes, "processes"
    )
    if workers == 0:
        raise ValueError("processes must be positive")

    # Enumeration needs OR-Tools; normal cache lookups should not import it.
    from common.math_utils import frobenius_solve

    totals = {}
    with ExitStack() as stack:
        cache = stack.enter_context(CharacterDecompositionCache(
            cache_directory, database_path=database_path,
            lie_executable=lie_executable, timeout=timeout, max_workers=1,
            lie_backend=lie_backend,
        ))
        executor = None
        for order in range(1, maximum + 1):
            solutions = sorted(frobenius_solve(range(1, order + 1), order))
            # Check only keys; a resumed build need not decode completed rows.
            rows = cache._connection().execute(
                "SELECT adams_powers FROM character_decompositions "
                "WHERE algebra=? AND dynkin_labels=? AND adams_order=?",
                (algebra, _json_key(labels), order),
            )
            known = {tuple(json.loads(row[0])) for row in rows}
            missing = [(algebra, labels, tuple(powers)) for powers in solutions
                       if tuple(powers) not in known]

            if missing and (workers == 1 or len(missing) == 1 or not any(labels)):
                # Batch commits also release completed work on an exception.
                with cache._batch_decompositions():
                    for request in missing:
                        cache.get_decomposition(*request)
            elif missing:
                if executor is None:
                    # Spawn avoids inheriting SQLite connections or OR-Tools'
                    # native thread state, and starts workers only as needed.
                    executor = stack.enter_context(ProcessPoolExecutor(
                        max_workers=workers, mp_context=get_context("spawn"),
                        initializer=_initialize_decomposition_worker,
                        initargs=(str(cache.database_path), cache.lie_executable,
                                  cache.max_nodes, cache.max_objects, cache.timeout, cache._lie_backend),
                    ))
                # Several batches per worker balance uneven tensor costs. Bound
                # batch size so completed decompositions can be saved promptly.
                batch_size = min(64, max(1, (len(missing) + 4 * workers - 1) // (4 * workers)))
                pending = {
                    executor.submit(_generate_decomposition_batch_in_worker,
                                    missing[start:start + batch_size])
                    for start in range(0, len(missing), batch_size)
                }
                for future in as_completed(pending):
                    pending.remove(future)
                    cache._write_decompositions(future.result())
                # The next order is not submitted until every batch is stored.

            totals[order] = len(solutions)
            if progress is not None:
                progress(order, len(solutions), len(missing))
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a complete Adams decomposition cache.")
    parser.add_argument("cartan_type", help="Cartan type, for example A2, C3, or G2")
    parser.add_argument("--dynkin-labels", nargs="+", type=int, required=True)
    parser.add_argument("--max-adams-order", "--max-order", type=int, required=True,
                        help="largest weighted Adams order to cache")
    parser.add_argument("--processes", type=int,
                        help="worker processes (default: CPU count; 1 runs serially)")
    paths = parser.add_mutually_exclusive_group()
    paths.add_argument("--cache-directory", type=Path)
    paths.add_argument("--cache-database", type=Path,
                       help="SQLite file (default: project-root char_decomposition_cache.db)")
    parser.add_argument("--lie-executable", default="lie")
    parser.add_argument("--lie-backend", choices=("auto", "native", "subprocess"), default="auto")
    parser.add_argument("--timeout", type=float, default=600,
                        help="timeout in seconds per LiE invocation (default: 600)")
    args = parser.parse_args(argv)

    def report(order, total, computed):
        print(f"Adams order {order}: {total} cached "
              f"({computed} computed, {total - computed} reused)", flush=True)

    try:
        totals = build_decomposition_cache(
            args.cartan_type, args.dynkin_labels, args.max_adams_order,
            cache_directory=args.cache_directory, database_path=args.cache_database,
            processes=args.processes, lie_executable=args.lie_executable,
            timeout=args.timeout, progress=report, lie_backend=args.lie_backend,
        )
    except (OSError, sqlite3.Error, ArithmeticError, TypeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    path = args.cache_database or (
        args.cache_directory / DEFAULT_CHAR_CACHE_DATABASE.name
        if args.cache_directory is not None else DEFAULT_CHAR_CACHE_DATABASE
    )
    print(f"Cache ready: {sum(totals.values())} decompositions in {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
