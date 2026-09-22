"""SageMath-backed representation data for compact simple Lie algebras.

Dynkin labels use SageMath's standard finite Cartan-type node numbering. For
the supported types this is Bourbaki numbering. The module supports

    A_r, B_r, C_r, D_r, E_6, E_7, E_8, F_4, G_2.

The associated compact group is assumed to be simply connected. Thus B and D
mean Spin groups and C means Sp. Global quotients are deliberately outside
this module because they are not determined by the Lie algebra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
import re
from typing import Any, Iterable

from common.number_utils import as_nonnegative_int, as_integer

try:
    from sage.all import CartanType, RootSystem, WeylCharacterRing
except ImportError as exc:  # pragma: no cover - exercised only outside Sage
    raise ImportError(
        "SageMath is required. Run this project with its Sage Python "
        "interpreter or with: sage -python <script>"
    ) from exc


# Highest weight of the representation
DynkinLabels = tuple[int, ...]


@dataclass(frozen=True)
class SimpleLieAlgebra:
    """Sage objects and derived invariants for one finite simple algebra."""

    cartan_type: str
    family: str
    rank: int
    dimension: int
    dual_coxeter_number: int
    adjoint_labels: DynkinLabels
    long_root_squared: Fraction
    index_set: tuple[int, ...]
    sage_cartan_type: Any = field(repr=False, compare=False)
    root_system: Any = field(repr=False, compare=False)
    weight_space: Any = field(repr=False, compare=False)
    character_ring: Any = field(repr=False, compare=False)

    @property
    def group(self) -> str:
        """Converts Cartan types to simply connected compact groups."""
        if self.family == "A":
            return f"SU({self.rank + 1})"
        if self.family == "B":
            return f"Spin({2 * self.rank + 1})"
        if self.family == "C":
            return f"Sp({self.rank})"
        if self.family == "D":
            return f"Spin({2 * self.rank})"
        return self.cartan_type

    @property
    def has_witten_anomaly(self) -> bool:
        """Identifies factors with the conventional four-dimensional mod-two gauge anomaly."""
        # A1 = C1 = SU(2) = Sp(1), and B2 = C2 = Spin(5) = Sp(2).
        return (
            (self.family == "A" and self.rank == 1)
            or self.family == "C"
            or (self.family == "B" and self.rank == 2)
        )


def _fraction(value: Any) -> Fraction:
    """Convert a Sage exact rational or integer to a stdlib Fraction."""
    return Fraction(str(value))


def parse_cartan_type(value: Any) -> tuple[str, int]:
    """Validate a supported finite simple Cartan type such as A4 or E6."""
    if not isinstance(value, str):
        raise ValueError("algebra must be a Cartan-type string such as A4 or E6")

    compact = re.sub(r"[\s_-]+", "", value).upper()
    match = re.fullmatch(r"([ABCDEFG])(\d+)", compact)
    if match is None:
        raise ValueError(
            f"invalid Cartan type {value!r}; expected A_r, B_r, C_r, D_r, "
            "E6, E7, E8, F4, or G2"
        )
    family, rank_text = match.groups()
    rank = int(rank_text)
    allowed = (
        (family == "A" and rank >= 1)
        or (family == "B" and rank >= 2)
        or (family == "C" and rank >= 2)
        or (family == "D" and rank >= 4)
        or (family == "E" and rank in {6, 7, 8})
        or (family == "F" and rank == 4)
        or (family == "G" and rank == 2)
    )
    if not allowed:
        raise ValueError(
            f"unsupported or non-simple Cartan type {family}{rank}; use A1 for "
            "B1/C1, A3 for D3, and a product of A1 factors for D2"
        )
    return family, rank


def get_lie_algebra(cartan_type: Any) -> SimpleLieAlgebra:
    """Return cached Sage root-system and character-ring data."""
    family, rank = parse_cartan_type(cartan_type)
    return _get_lie_algebra(family, rank)


@lru_cache(maxsize=None)
def _get_lie_algebra(family: str, rank: int) -> SimpleLieAlgebra:
    canonical = f"{family}{rank}"
    sage_cartan_type = CartanType(canonical)
    root_system = RootSystem(sage_cartan_type)
    character_ring = WeylCharacterRing(canonical, style="coroots")
    weight_space = character_ring.space()
    index_set = tuple(int(index) for index in sage_cartan_type.index_set())

    roots = tuple(weight_space.roots())
    long_root_squared = max(_fraction(root.scalar(root)) for root in roots)
    highest_root = weight_space.highest_root()
    simple_coroots = weight_space.simple_coroots()
    adjoint_labels = tuple(
        as_integer(
            highest_root.scalar(simple_coroots[index]),
            "highest-root Dynkin label",
        )
        for index in index_set
    )

    return SimpleLieAlgebra(
        cartan_type=canonical,
        family=family,
        rank=int(sage_cartan_type.rank()),
        dimension=int(sage_cartan_type.rank()) + len(roots),
        dual_coxeter_number=int(sage_cartan_type.dual_coxeter_number()),
        adjoint_labels=adjoint_labels,
        long_root_squared=long_root_squared,
        index_set=index_set,
        sage_cartan_type=sage_cartan_type,
        root_system=root_system,
        weight_space=weight_space,
        character_ring=character_ring,
    )


def _coerce_algebra(algebra: SimpleLieAlgebra | str) -> SimpleLieAlgebra:
    """Return SimpleLieAlgebra if algebra is in string representation."""
    if isinstance(algebra, SimpleLieAlgebra):
        return algebra
    return get_lie_algebra(algebra)


def diagram_automorphisms(
    algebra: SimpleLieAlgebra | str,
) -> tuple[tuple[int, ...], ...]:
    """Return all finite Dynkin-diagram symmetries in Sage node order.

    Each tuple permutes zero-based positions: apply it with
    ``tuple(labels[i] for i in permutation)``. Edge directions and labels
    are retained, so long and short roots cannot be exchanged. These are
    outer automorphisms of the simply connected group, not irrep duality.
    """
    return _diagram_automorphisms(_coerce_algebra(algebra).cartan_type)


@lru_cache(maxsize=None)
def _diagram_automorphisms(cartan_type: str) -> tuple[tuple[int, ...], ...]:
    cartan = CartanType(cartan_type)
    nodes = tuple(cartan.index_set())
    positions = {node: i for i, node in enumerate(nodes)}
    group = cartan.dynkin_diagram().automorphism_group(edge_labels=True)
    return tuple(sorted(
        tuple(positions[permutation(node)] for node in nodes)
        for permutation in group
    ))


def validate_dynkin_labels(
    algebra: SimpleLieAlgebra | str, labels: Iterable[Any]
) -> DynkinLabels:
    """Check whether it is a valid dynkin label."""
    algebra = _coerce_algebra(algebra)
    try:
        raw_labels = tuple(labels)
    except TypeError as exc:
        raise ValueError(
            "Dynkin labels must be an iterable of nonnegative integers"
        ) from exc
    result = tuple(
        as_nonnegative_int(value, f"Dynkin label {position}")
        for position, value in enumerate(raw_labels, start=1)
    )
    if len(result) != algebra.rank:
        raise ValueError(
            f"{algebra.cartan_type} needs {algebra.rank} Dynkin labels, "
            f"but {len(result)} were supplied"
        )
    return result


@lru_cache(maxsize=None)
def _irreducible_character(
    algebra: SimpleLieAlgebra, labels: DynkinLabels
) -> Any:
    fundamental_weights = algebra.weight_space.fundamental_weights()
    highest_weight = sum(
        (
            label * fundamental_weights[index]
            for index, label in zip(algebra.index_set, labels)
        ),
        algebra.weight_space.zero(),
    )
    return algebra.character_ring(highest_weight)


def _irrep(
    algebra: SimpleLieAlgebra, labels: Iterable[Any]
) -> tuple[DynkinLabels, Any]:
    """Return the character of irreducible representation of given dynkin labels."""
    validated = validate_dynkin_labels(algebra, labels)
    return validated, _irreducible_character(algebra, validated)


def conjugate_dynkin_labels(
    algebra: SimpleLieAlgebra | str, labels: Iterable[Any]
) -> DynkinLabels:
    """Return the highest-weight labels of the contragredient irrep."""
    algebra = _coerce_algebra(algebra)
    _, character = _irrep(algebra, labels)
    dual_weight = character.dual().highest_weight()
    simple_coroots = algebra.weight_space.simple_coroots()
    return tuple(
        as_integer(
            dual_weight.scalar(simple_coroots[index]),
            "dual Dynkin label",
        )
        for index in algebra.index_set
    )


def named_representation_labels(
    algebra: SimpleLieAlgebra | str, name: str
) -> DynkinLabels:
    """Map common physics representation names to Dynkin labels."""
    algebra = _coerce_algebra(algebra)
    if not isinstance(name, str):
        raise ValueError("representation name must be a string")
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "fund": "fundamental",
        "f": "fundamental",
        "antifund": "antifundamental",
        "anti_fundamental": "antifundamental",
        "bar_fundamental": "antifundamental",
        "adj": "adjoint",
        "trivial": "singlet",
        "vec": "vector",
        "sym": "symmetric",
        "two_index_symmetric": "symmetric",
        "anti": "antisymmetric",
        "asym": "antisymmetric",
        "two_index_antisymmetric": "antisymmetric",
        "bar_symmetric": "conjugate_symmetric",
        "bar_antisymmetric": "conjugate_antisymmetric",
        "conjugate_spinor": "cospinor",
    }
    key = aliases.get(key, key)
    result = [0] * algebra.rank

    fundamental_match = re.fullmatch(r"fundamental_?(\d+)", key)
    if fundamental_match is not None:
        node = int(fundamental_match.group(1))
        if not 1 <= node <= algebra.rank:
            raise ValueError(
                f"fundamental node must be between 1 and {algebra.rank}"
            )
        result[node - 1] = 1
        return tuple(result)

    minimal_node = {
        "E6": 1,
        "E7": 7,
        "E8": 8,
        "F4": 4,
        "G2": 1,
    }.get(algebra.cartan_type, 1)

    match key:
        case "singlet":
            pass
        case "adjoint":
            return algebra.adjoint_labels
        case "fundamental" | "antifundamental":
            result[minimal_node - 1] = 1
            if key == "antifundamental":
                return conjugate_dynkin_labels(algebra, result)
        case "vector" if algebra.family in {"B", "C", "D"}:
            result[0] = 1
        case "spinor" if algebra.family == "B":
            result[-1] = 1
        case "spinor" if algebra.family == "D":
            result[-2] = 1
        case "cospinor" if algebra.family == "D":
            result[-1] = 1
        case "symmetric" | "conjugate_symmetric" if algebra.family == "A":
            result[0] = 2
            if key == "conjugate_symmetric":
                return conjugate_dynkin_labels(algebra, result)
        case "antisymmetric" | "conjugate_antisymmetric" if algebra.family == "A":
            if algebra.rank >= 2:
                result[1] = 1
            if key == "conjugate_antisymmetric":
                return conjugate_dynkin_labels(algebra, result)
        case _:
            raise ValueError(
                f"unknown representation {name!r} for {algebra.cartan_type}; use "
                "Dynkin labels or a supported common name"
            )
    return tuple(result)


def representation_dimension(
    algebra: SimpleLieAlgebra | str, labels: Iterable[Any]
) -> int:
    """Return the Sage Weyl-character degree of an irreducible representation."""
    algebra = _coerce_algebra(algebra)
    _, character = _irrep(algebra, labels)
    return as_integer(character.degree(), "representation dimension")


def quadratic_casimir(
    algebra: SimpleLieAlgebra | str, labels: Iterable[Any]
) -> Fraction:
    """Return C2(R), normalized so C2(adjoint)=h dual."""
    algebra = _coerce_algebra(algebra)
    _, character = _irrep(algebra, labels)
    highest_weight = character.highest_weight()
    rho = algebra.weight_space.rho()
    numerator = _fraction(highest_weight.scalar(highest_weight + 2 * rho))
    return numerator / algebra.long_root_squared


def dynkin_index(
    algebra: SimpleLieAlgebra | str, labels: Iterable[Any]
) -> Fraction:
    """Return T(R)=dim(R) C2(R)/dim(g)."""
    algebra = _coerce_algebra(algebra)
    validated = validate_dynkin_labels(algebra, labels)
    return (
        Fraction(representation_dimension(algebra, validated), algebra.dimension)
        * quadratic_casimir(algebra, validated)
    )


@lru_cache(maxsize=None)
def _tits_parities(algebra: SimpleLieAlgebra) -> tuple[int, ...]:
    """Cache <omega_i, 2 rho^vee> modulo two for each fundamental weight."""
    space = algebra.weight_space
    # Coroots, rather than roots, are essential for non-simply-laced types.
    two_rho_dual = sum(
        (2 * root / root.scalar(root) for root in space.positive_roots()),
        space.zero(),
    )
    fundamental_weights = space.fundamental_weights()
    return tuple(
        as_integer(
            fundamental_weights[index].scalar(two_rho_dual),
            "Tits parity exponent",
        ) % 2
        for index in algebra.index_set
    )


def representation_reality(
    algebra: SimpleLieAlgebra | str, labels: Iterable[Any]
) -> str:
    """Classify an irrep by Tits' formula without computing tensor squares.

    Non-self-dual irreps are complex. For a self-dual highest weight lambda,
    the Frobenius-Schur indicator is (-1)^<lambda, 2 rho^vee>, where rho^vee
    is half the sum of positive coroots. Even exponents give real irreps;
    odd exponents give pseudoreal irreps. All pairings use exact arithmetic.

    See Theorem 3.6 of https://arxiv.org/abs/0704.0165.
    """
    algebra = _coerce_algebra(algebra)
    validated = validate_dynkin_labels(algebra, labels)
    if validated != conjugate_dynkin_labels(algebra, validated):
        return "complex"
    parity = sum(
        label * coefficient
        for label, coefficient in zip(validated, _tits_parities(algebra))
    ) % 2
    return "pseudoreal" if parity else "real"
