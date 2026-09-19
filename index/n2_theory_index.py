#!/usr/bin/env python3
"""Calculate the 4d N=2 index for simple and product groups with FORM and LiE.

The input conventions agree with ``anomalies.check_n2_anomalies``. FORM expands
the exact, truncated representation-valued plethystic exponential. LiE
decomposes Adams operations and intermediate character products; character
orthogonality extracts the final singlet coefficient. The SQLite cache in
``index.char_decomposition_cache`` stores decompositions and final singlet
coefficients using Cartan types, Dynkin labels and Adams powers.
The separate FORM expansion cache stores parsed terms by the raw program text.

NetworkX separates disconnected gauge sectors before expansion. Each sector
is calculated with FORM/LiE or reused from an optional N=2 theory database
connection, then the sector indices are multiplied with exact truncation.
Within a connected product, FORM keeps separate simple-factor characters.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, TYPE_CHECKING

import networkx as nx

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sage.all import LaurentPolynomialRing, QQ, sage_eval

from anomalies.check_n2_anomalies import (
    GaugeFactorData,
    HyperData,
    ProductHyperData,
    check_input_data,
)
from anomalies.lie_algebra import (
    DynkinLabels,
    conjugate_dynkin_labels,
    get_lie_algebra,
)
from common.number_utils import as_nonnegative_int
from common.form_utils import validate_form_threads
from index.char_decomposition_cache import (
    AdamsPowers,
    CharacterDecompositionCache,
)
from index.form_expansion_cache import (
    DEFAULT_FORM_CACHE_DATABASE,
    FormExpansionCache,
    IndexFormTerm,
)

if TYPE_CHECKING:
    from pymysql.connections import Connection


# A pair (gauge factor position, highest weight of representation)
CharacterSpec = tuple[int, DynkinLabels]
# Character of external tensor product representations
CharacterMonomial = tuple[CharacterSpec, ...]
# Character monomial after every character has been assigned a FORM character number
IndexedMonomial = tuple[int, ...]

INDEX_POLYNOMIAL_RING = LaurentPolynomialRing(
    QQ, 3, names=("t", "y", "u")
)
_INDEX_POLYNOMIAL_TEXT_PATTERN = re.compile(r"[0-9tyu+\-*/^()\s]+")


def _parse_input(
    data: dict[str, Any],
) -> tuple[tuple[GaugeFactorData, ...], list[HyperData | ProductHyperData]]:
    """Validate either JSON convention and return ordered gauge factors."""
    result = check_input_data(data)
    if result["errors"]:
        raise ValueError("; ".join(result["errors"]))

    if "gauge_factors" in result:
        factors = tuple(
            GaugeFactorData(
                factor["id"], get_lie_algebra(factor["algebra"])
            )
            for factor in result["gauge_factors"]
        )
    else:
        factors = (
            GaugeFactorData("gauge", get_lie_algebra(result["algebra"])),
        )
    return factors, result["hypermultiplets"]


def _matter_character_multiplicities(
    factors: tuple[GaugeFactorData, ...],
    hypermultiplets: list[HyperData | ProductHyperData],
) -> dict[CharacterMonomial, int]:
    """Return half-hyper multiplicities for external tensor products."""
    result: dict[CharacterMonomial, int] = {}
    for hyper in hypermultiplets:
        if isinstance(hyper, ProductHyperData):
            labels_by_factor = tuple(
                hyper.representations[factor.factor_id].labels
                for factor in factors
            )
        else:
            labels_by_factor = (hyper.representation.labels,)

        monomial = tuple(
            (position, labels)
            for position, labels in enumerate(labels_by_factor)
            if any(labels)
        )
        result[monomial] = result.get(monomial, 0) + hyper.number

        if hyper.kind == "full":
            conjugate = tuple(
                (
                    position,
                    conjugate_dynkin_labels(factor.algebra, labels),
                )
                for position, (factor, labels) in enumerate(
                    zip(factors, labels_by_factor, strict=True)
                )
                if any(labels)
            )
            result[conjugate] = result.get(conjugate, 0) + hyper.number

    return {monomial: value for monomial, value in result.items() if value}


def _character_basis(
    factors: tuple[GaugeFactorData, ...],
    hypermultiplets: list[HyperData | ProductHyperData],
) -> tuple[
    tuple[CharacterSpec, ...],
    tuple[int, ...],
    dict[IndexedMonomial, int],
]:
    """Return formal-character metadata for vectors and hypermultiplets."""
    matter = _matter_character_multiplicities(factors, hypermultiplets)
    vector_specs = tuple(
        (position, factor.algebra.adjoint_labels)
        for position, factor in enumerate(factors)
    )
    matter_specs = {
        specification for monomial in matter for specification in monomial
    }
    character_specs = vector_specs + tuple(
        sorted(matter_specs - set(vector_specs))
    )
    character_index = {
        specification: position
        for position, specification in enumerate(character_specs)
    }
    indexed_matter = {
        tuple(character_index[specification] for specification in monomial): value
        for monomial, value in matter.items()
    }
    vector_characters = tuple(character_index[item] for item in vector_specs)
    return character_specs, vector_characters, indexed_matter


def split_disconnected_sectors(
    factors: tuple[GaugeFactorData, ...],
    hypermultiplets: list[HyperData | ProductHyperData],
) -> list[tuple[tuple[GaugeFactorData, ...], list[HyperData | ProductHyperData]]]:
    """Partition charged matter by gauge connectivity; retain one free sector."""
    graph = nx.Graph()
    graph.add_nodes_from(factor.factor_id for factor in factors)
    supported_hypers = []
    for hyper in hypermultiplets:
        if not hyper.number:
            continue
        if isinstance(hyper, ProductHyperData):
            support = tuple(factor.factor_id for factor in factors
                            if any(hyper.representations[factor.factor_id].labels))
        else:
            support = (factors[0].factor_id,) if any(hyper.representation.labels) else ()
        if support:
            graph.add_edges_from((support[0], other) for other in support[1:])
        supported_hypers.append((hyper, support))

    # Preserve input factor order both within and between components, matching
    # the database's existing ordered-factor canonical identity.
    components = sorted(nx.connected_components(graph),
                        key=lambda component: min(i for i, factor in enumerate(factors)
                                                  if factor.factor_id in component))
    sector_factors = [tuple(factor for factor in factors if factor.factor_id in component)
                      for component in components]
    sector_hypers = [[] for _ in components]
    sector_by_factor = {factor_id: i for i, component in enumerate(components)
                        for factor_id in component}
    free_hypers = []
    for hyper, support in supported_hypers:
        if not support:
            if isinstance(hyper, ProductHyperData):
                hyper = replace(hyper, representations={}, beta_contributions={})
            else:
                hyper = ProductHyperData(
                    name=hyper.representation.name, representations={},
                    dimension=hyper.representation.dimension, reality=hyper.representation.reality,
                    number=hyper.number, kind=hyper.kind, beta_contributions={},
                )
            free_hypers.append(hyper)
            continue
        position = sector_by_factor[support[0]]
        component = sector_factors[position]
        if isinstance(hyper, ProductHyperData):
            if len(component) == 1:
                factor_id = component[0].factor_id
                hyper = HyperData(hyper.representations[factor_id], hyper.number,
                                  hyper.kind, hyper.beta_contributions[factor_id])
            else:
                hyper = replace(hyper,
                                representations={f.factor_id: hyper.representations[f.factor_id]
                                                 for f in component},
                                beta_contributions={f.factor_id: hyper.beta_contributions[f.factor_id]
                                                    for f in component})
        sector_hypers[position].append(hyper)
    sectors = list(zip(sector_factors, sector_hypers))
    if free_hypers:
        sectors.append(((), free_hypers))
    return sectors


def _truncate_index(polynomial: Any, order: int) -> Any:
    """Retain exact Laurent coefficients through the inclusive t cutoff."""
    return INDEX_POLYNOMIAL_RING({powers: coefficient
                                 for powers, coefficient in polynomial.dict().items()
                                 if powers[0] <= order})


def _build_form_program(
    order: int,
    character_count: int,
    vector_characters: tuple[int, ...],
    matter_multiplicities: dict[IndexedMonomial, int],
) -> str:
    """Build the representation-valued PE with degree-bounded products.

    All exponent terms have t-degree at least two. For a marked term of
    degree k, only exponent coefficients through order-k can contribute.
    The temporary marker w prevents a new product from being expanded again
    by a later degree branch in the same exponential step.
    """
    max_adams = order // 2
    derivative_order = order // 3
    character_names = ",".join(
        f"C{position}" for position in range(character_count)
    )

    letter_terms = [
        f"Kvec(t^j,y^j,u^j)*C{position}(j)"
        for position in vector_characters
    ]
    for monomial, multiplicity in sorted(matter_multiplicities.items()):
        prefix = "" if multiplicity == 1 else f"{multiplicity}*"
        characters = "".join(f"*C{position}(j)" for position in monomial)
        letter_terms.append(f"{prefix}Khyp(t^j,y^j,u^j){characters}")
    total_letters = "+".join(letter_terms) or "0"
    character_declaration = "d" + (f",{character_names}" if character_names else "")

    exponential_steps = ""
    if max_adams >= 2:
        exponential_steps = f"""#do i=2,{max_adams}
  Skip J,itotal;
  #do k=2,{order - 2}
    if (count(t,1) == `k');
      id z=1+w*sum_(idx1,2,{order}-`k',itotal[t^idx1]*t^idx1)/`i';
    endif;
  #enddo
  id z=1;
  id w=z;
  .sort:step `i';
#enddo
"""

    return f"""#: MaxTermSize 600000
Off statistics;
S m,n,y,idx1,idx2,j,z,w,u,t(:{order});
CF {character_declaration};
PolyRatFun d;
Function Kvec,Khyp;

L J=sum_(idx1,0,{derivative_order},m^idx1)
   *sum_(idx2,0,{derivative_order},n^idx2);
id m=t^3*y;
id n=t^3/y;
.sort

L itotal=sum_(j,1,{max_adams},({total_letters})/j);
id Kvec(t?,y?,u?)=J*(t^2*u^2-t^4/u^2-t^3*y-t^3/y+2*t^6);
id Khyp(t?,y?,u?)=J*(t^2/u-t^4*u);
Bracket t;
.sort

Skip J,itotal;
L I=z*itotal;
.sort
{exponential_steps}

L result=1+I;
id z=1;
.sort
Print result;
.end
"""


def _project_terms(
    terms: list[IndexFormTerm],
    factors: tuple[GaugeFactorData, ...],
    character_specs: tuple[CharacterSpec, ...],
    cache: CharacterDecompositionCache,
) -> dict[tuple[int, int, int], Fraction]:
    """Project independently to the singlet of every simple gauge factor."""
    structures = sorted({term.characters for term in terms})
    singlets_by_factor: list[
        dict[tuple[tuple[int, AdamsPowers], ...], int]
    ] = []
    for factor_position, factor in enumerate(factors):
        factor_structures = sorted(
            {
                tuple(
                    item
                    for item in structure
                    if character_specs[item[0]][0] == factor_position
                )
                for structure in structures
            }
        )
        products = [
            [(character_specs[character][1], powers) for character, powers in structure]
            for structure in factor_structures
        ]
        multiplicities = cache.get_singlet_multiplicities(
            factor.algebra.cartan_type,
            factor.algebra.rank,
            products,
        )
        singlets_by_factor.append(
            dict(zip(factor_structures, multiplicities, strict=True))
        )

    projected: dict[tuple[int, int, int], Fraction] = {}
    for term in terms:
        singlet_multiplicity = 1
        for factor_position, singlet_by_structure in enumerate(
            singlets_by_factor
        ):
            factor_structure = tuple(
                item
                for item in term.characters
                if character_specs[item[0]][0] == factor_position
            )
            singlet_multiplicity *= singlet_by_structure[factor_structure]
        coefficient = term.coefficient * singlet_multiplicity
        key = (term.t_power, term.y_power, term.u_power)
        updated = projected.get(key, Fraction(0)) + coefficient
        if updated:
            projected[key] = updated
        else:
            projected.pop(key, None)
    return projected


def _to_sage_polynomial(
    projected: dict[tuple[int, int, int], Fraction]
) -> Any:
    """Convert projected terms to one flat Sage Laurent polynomial."""
    index_ring = INDEX_POLYNOMIAL_RING
    t, y, u = index_ring.gens()
    result = index_ring.zero()
    for (t_power, y_power, u_power), coefficient in projected.items():
        sage_coefficient = QQ(coefficient.numerator) / coefficient.denominator
        result += (
            sage_coefficient
            * t**t_power
            * y**y_power
            * u**u_power
        )
    return result


def parse_index_polynomial(value: str) -> Any:
    """Restore a serialized superconformal index as a Sage polynomial."""
    if not isinstance(value, str):
        raise TypeError("superconformal index must be a string")
    if not value.strip() or _INDEX_POLYNOMIAL_TEXT_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid superconformal-index polynomial string")

    try:
        expression = sage_eval(
            value,
            locals=INDEX_POLYNOMIAL_RING.gens_dict(),
            preparse=True,
        )
        return INDEX_POLYNOMIAL_RING(expression)
    except (ArithmeticError, NameError, SyntaxError, TypeError, ValueError) as exc:
        raise ValueError(
            "invalid superconformal-index polynomial string"
        ) from exc


def calculate_index(
    data: dict[str, Any],
    order: int,
    *,
    char_cache_database_path: str | Path | None = None,
    form_cache_database_path: str | Path | None = None,
    lie_executable: str = "lie",
    form_executable: str = "form",
    tform_executable: str = "tform",
    form_threads: int = 1,
    timeout: float = 600,
    processes: int | None = None,
    theory_db_connection: Connection | None = None,
) -> Any:
    """Calculate the exact simple- or product-group index through ``t^order``.

    The default SQLite cache is ``char_decomposition_cache.db`` at the
    project root. Select another file with ``char_cache_database_path``.
    Parsed FORM expansions use ``form_expansion_cache.db`` in the same
    directory, unless ``form_cache_database_path`` selects another file.
    ``theory_db_connection`` optionally reuses stored sector indices with
    sufficient recorded precision; it is separate from the SQLite caches.
    ``form_threads=1`` uses FORM; larger counts use TFORM. This is separate
    from ``processes``, which controls LiE cache generation.
    """
    order = as_nonnegative_int(order, "order")
    form_threads = validate_form_threads(form_threads)
    factors, hypermultiplets = _parse_input(data)

    return calculate_index_internal(
        factors,
        hypermultiplets,
        order,
        char_cache_database_path=char_cache_database_path,
        form_cache_database_path=form_cache_database_path,
        lie_executable=lie_executable,
        form_executable=form_executable,
        tform_executable=tform_executable, form_threads=form_threads,
        timeout=timeout,
        processes=processes,
        theory_db_connection=theory_db_connection,
    )


def calculate_index_internal(
    factors: tuple[GaugeFactorData, ...],
    hypermultiplets: list[HyperData | ProductHyperData],
    order: int,
    *,
    char_cache_database_path: str | Path | None = None,
    form_cache_database_path: str | Path | None = None,
    lie_executable: str = "lie",
    form_executable: str = "form",
    tform_executable: str = "tform",
    form_threads: int = 1,
    timeout: float = 600,
    processes: int | None = None,
    theory_db_connection: Connection | None = None,
) -> Any:
    """Multiply indices of disconnected sectors of validated theory data.

    A supplied N=2 MySQL connection is borrowed for read-only sector lookups.
    Only indices with a known cutoff at least ``order`` are reused; other
    sectors use FORM/LiE. No sector rows are inserted and the connection is
    never committed, rolled back or closed here. Pass a connection owned by
    this process/thread, with the current schema already initialized.
    """
    order = as_nonnegative_int(order, "order")
    form_threads = validate_form_threads(form_threads)

    if order < 2:
        return _to_sage_polynomial({(0, 0, 0): Fraction(1)})

    result = INDEX_POLYNOMIAL_RING.one()
    calculated = {}
    for sector_factors, sector_hypers in split_disconnected_sectors(factors, hypermultiplets):
        # Identical sectors in one product need only one lookup/calculation.
        key = (tuple(f.algebra.cartan_type for f in sector_factors),
               tuple(sorted(_matter_character_multiplicities(sector_factors, sector_hypers).items())))
        if key not in calculated:
            sector_index = None
            if theory_db_connection is not None and sector_factors:
                # Import lazily: the database/property APIs also import index.
                from common.n2_theory_db import find_superconformal_index

                stored = find_superconformal_index(
                    theory_db_connection, sector_factors, sector_hypers, order=order,
                )
                if stored is not None:
                    try:
                        sector_index = parse_index_polynomial(stored)
                        if any(powers[0] < 0 for powers in sector_index.dict()):
                            sector_index = None
                    except (TypeError, ValueError):
                        # Invalid serialized data is not a usable cached index.
                        sector_index = None
            if sector_index is None:
                sector_index = _calculate_sector_index(
                    sector_factors, sector_hypers, order,
                    char_cache_database_path=char_cache_database_path,
                    form_cache_database_path=form_cache_database_path,
                    lie_executable=lie_executable, form_executable=form_executable,
                    tform_executable=tform_executable, form_threads=form_threads,
                    timeout=timeout, processes=processes,
                )
            calculated[key] = _truncate_index(sector_index, order)
        result = _truncate_index(result * calculated[key], order)
    return result


def _calculate_sector_index(
    factors: tuple[GaugeFactorData, ...],
    hypermultiplets: list[HyperData | ProductHyperData],
    order: int,
    *,
    char_cache_database_path: str | Path | None = None,
    form_cache_database_path: str | Path | None = None,
    lie_executable: str = "lie",
    form_executable: str = "form",
    tform_executable: str = "tform",
    form_threads: int = 1,
    timeout: float = 600,
    processes: int | None = None,
) -> Any:
    """Expand and project one complete sector using the existing FORM path."""
    character_specs, vector_characters, matter_multiplicities = _character_basis(
        factors, hypermultiplets
    )
    program = _build_form_program(
        order,
        len(character_specs),
        vector_characters,
        matter_multiplicities,
    )
    with CharacterDecompositionCache(
        database_path=char_cache_database_path,
        lie_executable=lie_executable,
        timeout=timeout,
        max_workers=processes,
    ) as cache:
        if form_cache_database_path is None:
            form_cache_database_path = (
                cache.database_path.parent / DEFAULT_FORM_CACHE_DATABASE.name
            )
        with FormExpansionCache(
            database_path=form_cache_database_path,
            form_executable=form_executable,
            tform_executable=tform_executable, form_threads=form_threads,
            timeout=timeout,
        ) as form_cache:
            terms = form_cache.get_expansion(program)
        projected = _project_terms(terms, factors, character_specs, cache)
    return _to_sage_polynomial(projected)


def calculate_index_from_file(
    path: str | Path,
    order: int,
    **kwargs: Any,
) -> Any:
    """Load either existing theory JSON format and calculate its index."""
    input_path = Path(path)
    with input_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return calculate_index(data, order, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="path to the theory JSON file")
    parser.add_argument(
        "--order",
        type=int,
        required=True,
        help="largest power of t retained in the index",
    )
    parser.add_argument(
        "--char-cache-database",
        type=Path,
        help="character SQLite cache file (default: project-root char_decomposition_cache.db)",
    )
    parser.add_argument(
        "--form-cache-database",
        type=Path,
        help="FORM expansion cache file (default: beside the character cache)",
    )
    parser.add_argument(
        "--processes",
        type=int,
        help="LiE cache-generation processes (default: available CPUs; 1 disables)",
    )
    parser.add_argument("--form-executable", default="form")
    parser.add_argument("--tform-executable", default="tform")
    parser.add_argument("--form-threads", type=int, default=1,
                        help="1 uses FORM; larger values use TFORM with this many workers")
    args = parser.parse_args(argv)

    try:
        result = calculate_index_from_file(
            args.input,
            args.order,
            char_cache_database_path=args.char_cache_database,
            form_cache_database_path=args.form_cache_database,
            processes=args.processes,
            form_executable=args.form_executable,
            tform_executable=args.tform_executable, form_threads=args.form_threads,
        )
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        ArithmeticError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
