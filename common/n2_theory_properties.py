#!/usr/bin/env python3
"""Calculate shared properties of four-dimensional N=2 Lagrangian theories.

The input JSON schema is the same one accepted by
``anomalies.check_n2_anomalies``. ``calculate_n2_theory_properties`` calculates
the connected continuous flavor symmetry at the massless point, the local
complex dimension of the conformal manifold, and the conformal central charges.
It never calculates indices or the Coulomb spectrum.

``calculate_n2_theory_indices`` is a separate, explicit calculation of the full
superconformal index, Coulomb-branch index, and Coulomb-branch spectrum. It also
returns the requested inclusive cutoffs, which must be retained alongside the
index strings: their highest nonzero powers need not equal their precision.
Database persistence and replacement of stored results are handled by callers.

The CLI calculates basic properties by default; use ``--indices`` for the
separate index calculation, optionally with ``--index-order`` and
``--coulomb-max-dimension``.
"""

from __future__ import annotations

import argparse
import multiprocessing
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
from index.n2_theory_coulomb_branches import (
    calculate_lagrangian_coulomb_branch_index,
    coulomb_branch_spectrum_from_gauge_factors,
)
from index.n2_theory_index import calculate_index_internal, split_disconnected_sectors
from index.char_decomposition_cache import DEFAULT_CHAR_CACHE_DATABASE

if __package__:
    from .json_utils import json_text
    from .number_utils import as_nonnegative_fraction, as_nonnegative_int
else:
    from common.json_utils import json_text
    from common.number_utils import as_nonnegative_fraction, as_nonnegative_int

RepresentationKey = tuple[tuple[str, DynkinLabels], ...]

INDEX_MAX_ORDER = 18
C_INDEX_MAX_ORDER = 90
CHAR_CACHE_DATABASE_PATH = DEFAULT_CHAR_CACHE_DATABASE
LIE_EXECUTABLE = "lie"
FORM_EXECUTABLE = "form"
TFORM_EXECUTABLE = "tform"
FORM_THREADS = 1
DEFAULT_TIMEOUT = 600
DEFAULT_PROCESS_COUNT = multiprocessing.cpu_count()


@dataclass
class _FlavorBlock:
    """Multiplicities of one irreducible total gauge representation."""

    key: RepresentationKey
    reality: str
    full_multiplicity: int = 0
    half_multiplicity: int = 0


def _canonical_complex_key(
    key: RepresentationKey, factor_algebras: dict[str, str]
) -> RepresentationKey:
    """Prefer lower-numbered Dynkin nodes under simultaneous conjugation."""
    conjugate_key = tuple(
        (
            factor_id,
            conjugate_dynkin_labels(factor_algebras[factor_id], labels),
        )
        for factor_id, labels in key
    )
    return max(key, conjugate_key)


def _add_flavor_block(
    blocks: dict[RepresentationKey, _FlavorBlock],
    key: RepresentationKey,
    reality: str,
    kind: str,
    number: int,
    factor_algebras: dict[str, str],
) -> None:
    """Accumulate multiplicities for hypermultiplets."""
    if number == 0:
        return
    if reality == "complex":
        key = _canonical_complex_key(key, factor_algebras)

    block = blocks.get(key)
    if block is None:
        block = _FlavorBlock(key=key, reality=reality)
        blocks[key] = block
    elif block.reality != reality:
        raise ArithmeticError(
            "equivalent gauge representations received different reality types"
        )

    if kind == "full":
        block.full_multiplicity += number
    else:
        block.half_multiplicity += number


def _simple_flavor_blocks(
    anomaly_result: dict[str, Any],
) -> tuple[dict[RepresentationKey, _FlavorBlock], dict[str, str]]:
    """Group all hypers in simple gauge group with the same representation."""
    factor_id = "gauge"
    factor_algebras = {factor_id: anomaly_result["algebra"]}
    blocks: dict[RepresentationKey, _FlavorBlock] = {}
    hypers: list[HyperData] = anomaly_result["hypermultiplets"]

    for hyper in hypers:
        representation = hyper.representation
        key = ((factor_id, representation.labels),)
        _add_flavor_block(
            blocks,
            key,
            representation.reality,
            hyper.kind,
            hyper.number,
            factor_algebras,
        )
    return blocks, factor_algebras


def _product_flavor_blocks(
    anomaly_result: dict[str, Any],
) -> tuple[dict[RepresentationKey, _FlavorBlock], dict[str, str]]:
    """Group all hypers in product gauge group with the same representation."""
    factors = anomaly_result["gauge_factors"]
    factor_ids = [factor["id"] for factor in factors]
    factor_algebras = {
        factor["id"]: factor["algebra"] for factor in factors
    }
    blocks: dict[RepresentationKey, _FlavorBlock] = {}
    hypers: list[ProductHyperData] = anomaly_result["hypermultiplets"]

    for hyper in hypers:
        key = tuple(
            (factor_id, hyper.representations[factor_id].labels)
            for factor_id in factor_ids
        )
        _add_flavor_block(
            blocks,
            key,
            hyper.reality,
            hyper.kind,
            hyper.number,
            factor_algebras,
        )
    return blocks, factor_algebras


def _representation_data(
    key: RepresentationKey, factor_algebras: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Serialize the gauge representation associated with a flavor factor."""
    return {
        factor_id: {
            "algebra": factor_algebras[factor_id],
            "dynkin_labels": list(labels),
        }
        for factor_id, labels in key
    }


def _flavor_factor(
    block: _FlavorBlock, factor_algebras: dict[str, str]
) -> dict[str, Any]:
    """Calculate the flavor symmetry for a flavor block."""
    match block.reality:
        case "complex":
            if block.half_multiplicity:
                raise ArithmeticError(
                    "a complex representation cannot contain half hypers"
                )
            multiplicity = block.full_multiplicity
            group = f"U({multiplicity})"
            lie_algebra = f"u({multiplicity})"
            rank = multiplicity
            dimension = multiplicity**2
            half_hyper_units = 2 * multiplicity
        case "real":
            if block.half_multiplicity:
                raise ArithmeticError(
                    "a real representation cannot contain half hypers"
                )
            multiplicity = block.full_multiplicity
            group = f"Sp({multiplicity})"
            lie_algebra = f"sp({multiplicity})"
            rank = multiplicity
            dimension = multiplicity * (2 * multiplicity + 1)
            half_hyper_units = 2 * multiplicity
        case "pseudoreal":
            half_hyper_units = (
                block.half_multiplicity + 2 * block.full_multiplicity
            )
            group = f"SO({half_hyper_units})"
            lie_algebra = f"so({half_hyper_units})"
            rank = half_hyper_units // 2
            dimension = half_hyper_units * (half_hyper_units - 1) // 2
        case _:
            raise ArithmeticError(
                f"unexpected representation reality {block.reality!r}"
            )

    return {
        "group": group,
        "lie_algebra": lie_algebra,
        "rank": rank,
        "dimension": dimension,
        "representation_reality": block.reality,
        "full_hypermultiplets": block.full_multiplicity,
        "half_hypermultiplets": block.half_multiplicity,
        "half_hyper_units": half_hyper_units,
        "gauge_representation": _representation_data(
            block.key, factor_algebras
        ),
    }


def _calculate_flavor_symmetry(
    anomaly_result: dict[str, Any],
) -> dict[str, Any]:
    """Calculate the flavor symmetry of the theory."""
    if "gauge_factors" in anomaly_result:
        blocks, factor_algebras = _product_flavor_blocks(anomaly_result)
    else:
        blocks, factor_algebras = _simple_flavor_blocks(anomaly_result)

    factors = [
        _flavor_factor(block, factor_algebras) for block in blocks.values()
    ]
    nontrivial_groups = [
        factor["group"] for factor in factors if factor["dimension"] > 0
    ]
    return {
        "connected_group": (
            " x ".join(nontrivial_groups) if nontrivial_groups else "trivial"
        ),
        "rank": sum(factor["rank"] for factor in factors),
        "dimension": sum(factor["dimension"] for factor in factors),
        "factors": factors,
    }


def _exactly_marginal_gauge_couplings(
    anomaly_result: dict[str, Any],
) -> list[str]:
    """Return exactly marginal gauge couplings."""
    if not anomaly_result["lagrangian_scft_candidate"]:
        return []
    if "gauge_factors" in anomaly_result:
        return [factor["id"] for factor in anomaly_result["gauge_factors"]]
    return ["gauge"]


def _calculate_central_charges(
    anomaly_result: dict[str, Any],
) -> dict[str, Fraction]:
    """Calculate the conformal central charges from effective multiplets."""
    if "gauge_factors" in anomaly_result:
        n_v = 0
        for factor in anomaly_result["gauge_factors"]:
            n_v += get_lie_algebra(factor["algebra"]).dimension
        n_h = Fraction(0)
        for hyper in anomaly_result["hypermultiplets"]:
            e_i = Fraction(1) if hyper.kind == "full" else Fraction(1, 2)
            n_h += hyper.number * e_i * hyper.dimension
    else:
        n_v = get_lie_algebra(anomaly_result["algebra"]).dimension
        n_h = Fraction(0)
        for hyper in anomaly_result["hypermultiplets"]:
            e_i = Fraction(1) if hyper.kind == "full" else Fraction(1, 2)
            n_h += hyper.number * e_i * hyper.representation.dimension

    a = Fraction(5, 24) * n_v + Fraction(1, 24) * n_h
    c = Fraction(1, 6) * n_v + Fraction(1, 12) * n_h

    if a <= 0 or c <= 0:
        raise ValueError("Central charges must be positive.")

    if 4 * (2 * a - c) != n_v:
        raise ArithmeticError(
            "Central-charge and vector-multiplet counts disagree."
        )

    if 4 * (5 * c - 4 * a) != n_h:
        raise ArithmeticError(
            "Central-charge and hypermultiplet counts disagree."
        )

    return {"a": a, "c": c}


def _extract_gauge_factors(
    anomaly_result: dict[str, Any],
) -> tuple[GaugeFactorData, ...]:
    """Extract gauge factors from anomaly result."""
    if "gauge_factors" in anomaly_result:
        factors = tuple(
            GaugeFactorData(
                factor["id"], get_lie_algebra(factor["algebra"])
            )
            for factor in anomaly_result["gauge_factors"]
        )
    else:
        factors = (
            GaugeFactorData(
                "gauge", get_lie_algebra(anomaly_result["algebra"])
            ),
        )

    return factors


def _get_disconnected_sectors(
    anomaly_result: dict[str, Any]
) -> tuple[tuple[str]]:
    """Return disconnected sectors for theories with gauge factors and hypermultiplets."""
    factors = _extract_gauge_factors(anomaly_result)
    hypermultiplets = anomaly_result["hypermultiplets"]
    sector_data = split_disconnected_sectors(factors, hypermultiplets)
    return tuple(
        tuple(factor.factor_id for factor in sector_factors)
        for sector_factors, _ in sector_data
    )


def _calculate_superconformal_index(
    anomaly_result: dict[str, Any],
    order: int,
    **options,
) -> Any:
    """Calculate the superconformal index."""
    factors = _extract_gauge_factors(anomaly_result)
    hypermultiplets = anomaly_result["hypermultiplets"]
    defaults = dict(
        char_cache_database_path=CHAR_CACHE_DATABASE_PATH,
        lie_executable=LIE_EXECUTABLE, form_executable=FORM_EXECUTABLE,
        tform_executable=TFORM_EXECUTABLE, form_threads=FORM_THREADS,
        timeout=DEFAULT_TIMEOUT, processes=DEFAULT_PROCESS_COUNT,
    )
    defaults.update(options)
    return calculate_index_internal(
        factors,
        hypermultiplets,
        order,
        **defaults,
    )


def _calculate_coulomb_branch_index(
    anomaly_result: dict[str, Any],
    max_dimension: Fraction,
) -> Any:
    """Calculate the Coulomb-branch index."""
    factors = _extract_gauge_factors(anomaly_result)
    return calculate_lagrangian_coulomb_branch_index(
        factors,
        max_dimension,
        form_executable=FORM_EXECUTABLE,
        tform_executable=TFORM_EXECUTABLE, form_threads=FORM_THREADS,
        timeout=DEFAULT_TIMEOUT,
    )


def _calculate_coulomb_branch_spectrum(
    anomaly_result: dict[str, Any]
) -> tuple[Fraction, ...]:
    """Calculate the Coulomb-branch spectrum."""
    factors = _extract_gauge_factors(anomaly_result)
    return tuple(
        Fraction(dim)
        for dim in coulomb_branch_spectrum_from_gauge_factors(factors)
    )


def _validated_anomaly_result(data: dict[str, Any]) -> dict[str, Any]:
    """Check the input and return its parsed anomaly result."""
    anomaly_result = check_input_data(data)
    if anomaly_result["errors"]:
        messages = "; ".join(anomaly_result["errors"])
        raise ValueError(f"invalid theory input: {messages}")
    return anomaly_result


def calculate_n2_theory_properties(data: dict[str, Any]) -> dict[str, Any]:
    """Calculate basic properties without any index or Coulomb calculation.

    Index and Coulomb-spectrum keys are omitted. Use
    :func:`calculate_n2_theory_indices` separately for those results. Invalid
    input raises ``ValueError``; well-formed non-SCFT input retains its flavor
    result, with central charges and conformal-manifold dimension set to None.
    """
    anomaly_result = _validated_anomaly_result(data)

    flavor_symmetry = _calculate_flavor_symmetry(anomaly_result)
    marginal_couplings = _exactly_marginal_gauge_couplings(anomaly_result)
    conformal_dimension = (
        len(marginal_couplings)
        if anomaly_result["lagrangian_scft_candidate"]
        else None
    )
    central_charges = (
        _calculate_central_charges(anomaly_result)
        if anomaly_result["lagrangian_scft_candidate"]
        else None
    )
    disconnected_sectors = (
        _get_disconnected_sectors(anomaly_result)
        if anomaly_result["lagrangian_scft_candidate"]
        else None
    )
    return {
        "group": anomaly_result["group"],
        "lagrangian_scft_candidate": anomaly_result[
            "lagrangian_scft_candidate"
        ],
        "flavor_symmetry": flavor_symmetry,
        "conformal_manifold_dimension": conformal_dimension,
        "exactly_marginal_gauge_couplings": marginal_couplings,
        "central_charges": central_charges,
        "disconnected_sectors": disconnected_sectors,
    }


def calculate_n2_theory_indices(
    data: dict[str, Any],
    *,
    order: Any | None = None,
    max_dimension: Any | None = None,
) -> dict[str, Any]:
    """Calculate index-related properties separately from basic properties.

    ``order`` is the inclusive nonnegative integer cutoff in t (default
    ``INDEX_MAX_ORDER``, 18). ``max_dimension`` is the inclusive exact rational
    Coulomb scaling-dimension cutoff (default ``C_INDEX_MAX_ORDER``, 90).
    The Coulomb spectrum itself is complete, independent of either cutoff.

    Index values are serialized strings; the spectrum is a sorted tuple of
    exact ``Fraction`` dimensions, retaining multiplicities. The
    ``superconformal_index_order`` and ``coulomb_branch_index_max_dimension``
    fields record the actual requested cutoffs, including when the boundary
    coefficients vanish. Storage code compares these cutoffs independently
    before replacing either index; this function does no writes
    to the theory database and does not enforce a stored-result update policy.

    Invalid input raises ``ValueError``. For a well-formed non-SCFT candidate,
    all five fields are None and no index or spectrum calculation runs.
    """
    order = as_nonnegative_int(
        INDEX_MAX_ORDER if order is None else order, "order"
    )
    max_dimension = as_nonnegative_fraction(
        C_INDEX_MAX_ORDER if max_dimension is None else max_dimension,
        "max_dimension",
    )
    anomaly_result = _validated_anomaly_result(data)
    if not anomaly_result["lagrangian_scft_candidate"]:
        return {
            "superconformal_index": None,
            "superconformal_index_order": None,
            "coulomb_branch_index": None,
            "coulomb_branch_index_max_dimension": None,
            "coulomb_branch_spectrum": None,
        }
    return {
        "superconformal_index": str(
            _calculate_superconformal_index(anomaly_result, order)
        ),
        "superconformal_index_order": order,
        "coulomb_branch_index": str(
            _calculate_coulomb_branch_index(anomaly_result, max_dimension)
        ),
        "coulomb_branch_index_max_dimension": max_dimension,
        "coulomb_branch_spectrum": _calculate_coulomb_branch_spectrum(
            anomaly_result
        ),
    }


def calculate_n2_theory_properties_from_file(
    path: str | Path,
) -> dict[str, Any]:
    """Load a theory JSON file and calculate only its basic properties."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return calculate_n2_theory_properties(data)


def calculate_central_charges(
    data: dict[str, Any],
) -> dict[str, Fraction] | None:
    """Calculate central charges for a Lagrangian SCFT candidate."""
    anomaly_result = _validated_anomaly_result(data)
    if not anomaly_result["lagrangian_scft_candidate"]:
        return None
    return _calculate_central_charges(anomaly_result)


def calculate_coulomb_branch_index(
    data: dict[str, Any], *, max_dimension: Any | None = None
) -> Any:
    """Calculate the raw Coulomb index through an inclusive dimension cutoff."""
    max_dimension = as_nonnegative_fraction(
        C_INDEX_MAX_ORDER if max_dimension is None else max_dimension,
        "max_dimension",
    )
    anomaly_result = _validated_anomaly_result(data)
    if not anomaly_result["lagrangian_scft_candidate"]:
        return None
    return _calculate_coulomb_branch_index(anomaly_result, max_dimension)


def calculate_coulomb_branch_spectrum(
    data: dict[str, Any],
) -> tuple[Fraction, ...] | None:
    """Calculate the Coulomb spectrum of a Lagrangian SCFT candidate."""
    anomaly_result = _validated_anomaly_result(data)
    if not anomaly_result["lagrangian_scft_candidate"]:
        return None
    return _calculate_coulomb_branch_spectrum(anomaly_result)


def calculate_superconformal_index(
    data: dict[str, Any], *, order: Any | None = None, **options,
) -> Any:
    """Calculate the full index; options override calculate_index_internal tools/caches."""
    order = as_nonnegative_int(
        INDEX_MAX_ORDER if order is None else order, "order"
    )
    anomaly_result = _validated_anomaly_result(data)
    if not anomaly_result["lagrangian_scft_candidate"]:
        return None
    return _calculate_superconformal_index(anomaly_result, order, **options)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="path to the theory JSON file")
    parser.add_argument(
        "--indices", action="store_true",
        help="calculate only index-related properties",
    )
    parser.add_argument(
        "--index-order", type=int,
        help="inclusive t cutoff for --indices (default: 18)",
    )
    parser.add_argument(
        "--coulomb-max-dimension",
        help="inclusive exact Coulomb dimension cutoff for --indices (default: 90)",
    )
    args = parser.parse_args(argv)
    if not args.indices and (
        args.index_order is not None or args.coulomb_max_dimension is not None
    ):
        parser.error("index cutoffs require --indices")
    try:
        if args.indices:
            with args.input.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            result = calculate_n2_theory_indices(
                data,
                order=args.index_order,
                max_dimension=args.coulomb_max_dimension,
            )
        else:
            result = calculate_n2_theory_properties_from_file(args.input)
    except (
        OSError,
        json.JSONDecodeError,
        ValueError,
        ArithmeticError,
        RuntimeError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json_text(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
