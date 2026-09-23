"""Enumerate bounded irreps and conformal matter candidates for gauge groups."""

from collections.abc import Iterable, Mapping
from fractions import Fraction
from math import lcm
from typing import Any

from anomalies.check_n2_anomalies import GaugeFactorData
from anomalies.lie_algebra import (
    DynkinLabels,
    conjugate_dynkin_labels,
    dynkin_index,
    representation_dimension, get_lie_algebra, representation_reality,
)
from common.math_utils import frobenius_solve, frobenius_system_solve
from common.number_utils import as_positive_fraction


def enumerate_irreps(
    gauge_factor: GaugeFactorData,
    *,
    max_index: Fraction | None = None,
    inclusive: bool = True,
    include_singlet: bool = False,
    identify_conjugates: bool = True,
) -> list[tuple[DynkinLabels, Fraction]]:
    """Return sorted pairs of Dynkin labels and exact indices within a bound.

    The default bound is twice the adjoint index (``2 * h_dual``), allowing
    half-hyper candidates. Equality is allowed unless ``inclusive=False``.
    The singlet is omitted unless ``include_singlet=True``.
    By default, retain the lexicographically larger label tuple from each
    conjugate pair, favoring lower-numbered Dynkin nodes (for example, the
    SU(n) fundamental over its antifundamental). Set
    ``identify_conjugates=False`` to return both.

    The index increases with each Dynkin label, so a rejected tuple and all
    componentwise larger tuples can be pruned from the finite search.
    """
    algebra = gauge_factor.algebra
    bound = as_positive_fraction(
        2 * algebra.dual_coxeter_number if max_index is None else max_index,
        "max_index",
    )

    zero: DynkinLabels = (0,) * algebra.rank
    pending = [zero]
    seen = {zero}
    result = []

    while pending:
        labels = pending.pop()
        index = dynkin_index(algebra, labels)
        if index > bound or (index == bound and not inclusive):
            continue

        if include_singlet or labels != zero:
            result.append((labels, index))

        for i in range(algebra.rank):
            child = labels[:i] + (labels[i] + 1,) + labels[i + 1:]
            if child not in seen:
                seen.add(child)
                pending.append(child)

    # Conjugation filters the output only; it must not prune search branches.
    if identify_conjugates:
        result = [
            (labels, index)
            for labels, index in result
            if labels >= conjugate_dynkin_labels(algebra, labels)
        ]
    return sorted(result, key=lambda x: x[0])


def enumerate_product_irreps(
    gauge_factors: Iterable[GaugeFactorData],
    *,
    max_indices: Mapping[str, Fraction] | None = None,
    inclusive: bool = True,
    include_singlet: bool = False,
    identify_conjugates: bool = True,
) -> list[tuple[dict[str, DynkinLabels], dict[str, Fraction]]]:
    """Enumerate tensor-product irreps satisfying every factor's index bound.

    For ``R = R_1 x ... x R_k``, the index for factor ``i`` is
    ``T_i(R) = T(R_i) * product_{j != i} dim(R_j)``. Each bound defaults to
    twice that factor's adjoint index (``2 * h_dual``), allowing half-hyper
    candidates. ``max_indices`` overrides bounds by factor ID. Unknown IDs
    and nonpositive or inexact bounds are rejected.

    Return pairs ``(labels_by_id, indices_by_id)``, including every factor
    in input order and sorted lexicographically by the tuples of labels in
    that order. Indices include spectator dimensions and are exact Fractions.
    Equality is allowed unless ``inclusive=False``. Singlets of individual
    factors are always considered; ``include_singlet`` controls only the
    representation trivial under the entire group.

    By default, identify a product with its simultaneous conjugate under all
    factors, keeping the lexicographically larger tuple of label tuples in
    input factor order. This favors lower-numbered Dynkin nodes in the first
    factor where the labels differ. Set ``identify_conjugates=False`` to
    return both.

    The input must contain at least one factor with distinct nonempty IDs.
    These bounds do not impose matter reality, anomaly cancellation, or
    conformality of a whole matter content.
    """
    try:
        factors = tuple(gauge_factors)
    except TypeError as exc:
        raise ValueError(
            "gauge_factors must be a nonempty iterable of GaugeFactorData objects"
        ) from exc
    if not factors or any(
        not isinstance(factor, GaugeFactorData) for factor in factors
    ):
        raise ValueError(
            "gauge_factors must be a nonempty iterable of GaugeFactorData objects"
        )

    factor_ids = tuple(factor.factor_id for factor in factors)
    if any(
        not isinstance(factor_id, str) or not factor_id.strip()
        for factor_id in factor_ids
    ):
        raise ValueError("gauge factor IDs must be nonempty strings")
    if len(set(factor_ids)) != len(factor_ids):
        raise ValueError("gauge factor IDs must be distinct")

    overrides = {} if max_indices is None else max_indices
    if not isinstance(overrides, Mapping):
        raise ValueError("max_indices must be a mapping keyed by gauge factor ID")
    unknown_ids = set(overrides) - set(factor_ids)
    if unknown_ids:
        unknown_text = ", ".join(sorted(map(str, unknown_ids)))
        raise ValueError(f"unknown gauge factor(s): {unknown_text}")
    bounds = tuple(
        as_positive_fraction(
            overrides.get(factor.factor_id, 2 * factor.algebra.dual_coxeter_number),
            f"max_indices[{factor.factor_id!r}]",
        )
        for factor in factors
    )

    # Spectator dimensions are at least one, so every allowed product uses
    # only factor irreps that already satisfy their individual index bound.
    candidates = [
        [
            (labels, index, representation_dimension(factor.algebra, labels))
            for labels, index in enumerate_irreps(
                factor,
                max_index=bound,
                inclusive=inclusive,
                include_singlet=True,
                identify_conjugates=False,
            )
        ]
        for factor, bound in zip(factors, bounds)
    ]

    result = []
    pending = [((), (), 1)]
    while pending:
        labels, indices, total_dimension = pending.pop()
        position = len(labels)
        if position == len(factors):
            if include_singlet or any(indices):
                result.append(
                    (dict(zip(factor_ids, labels)), dict(zip(factor_ids, indices)))
                )
            continue

        # Reverse insertion makes the depth-first output lexicographically sorted.
        for rep_labels, rep_index, dimension in reversed(candidates[position]):
            next_indices = tuple(value * dimension for value in indices) + (
                rep_index * total_dimension,
            )
            if any(
                value > bound or (value == bound and not inclusive)
                for value, bound in zip(next_indices, bounds)
            ):
                # Later spectator factors can only increase these indices.
                continue
            pending.append(
                (labels + (rep_labels,), next_indices, total_dimension * dimension)
            )

    # Keep all factor candidates above: independently identifying conjugates
    # would lose products such as (3, 3bar) when constructing SU(3) x SU(3).
    if identify_conjugates:
        result = [
            (labels, indices)
            for labels, indices in result
            if tuple(labels[factor_id] for factor_id in factor_ids)
            >= tuple(
                conjugate_dynkin_labels(factor.algebra, labels[factor.factor_id])
                for factor in factors
            )
        ]
    return result


def enumerate_simple_theory_candidates(
    gauge_group: str,
) -> list[dict[str, Any]]:
    """Enumerate all possible Lagrangian theory from given simple gauge group.
    gauge_group is the Cartan type of the lie algebra.
    """
    gauge_factor = GaugeFactorData("gauge", get_lie_algebra(gauge_group))
    irreps = enumerate_irreps(gauge_factor)
    realities = [
        representation_reality(
            gauge_factor.algebra,
            labels
        ) for labels, _ in irreps
    ]

    scale = lcm(*(index.denominator for _, index in irreps))
    scaled_indices = [(index * scale).numerator for _, index in irreps]
    scaled_coxeter = gauge_factor.algebra.dual_coxeter_number * scale
    for i in range(len(realities)):
        if realities[i] != "pseudoreal":
            scaled_indices[i] *= 2

    solutions = frobenius_solve(scaled_indices, 2 * scaled_coxeter)
    candidates = []
    for solution in solutions:
        theory = {
            "algebra": gauge_group,
            "hypermultiplets": []
        }
        for i in range(len(solution)):
            number = solution[i]
            if number == 0:
                continue

            theory["hypermultiplets"].append(
                {
                    "dynkin_labels": irreps[i][0],
                    "number": number,
                    "kind": "half" if realities[i] == "pseudoreal" else "full"
                }
            )

        candidates.append(theory)

    return candidates


def _has_single_gauge_sector(
    factor_count: int,
    supports: Iterable[tuple[int, ...]],
    multiplicities: Iterable[int],
) -> bool:
    """Check connectivity using only the supports of present matter types.

    The supports contain factor positions, precomputed once per enumeration.
    All factors are vertices, including ones with no multiply charged matter.
    Multiplicities are the nonnegative integers returned by the beta solver.
    """
    if factor_count == 1:
        return True
    parents = list(range(factor_count))
    components = factor_count

    def root(position):
        while parents[position] != position:
            parents[position] = parents[parents[position]]
            position = parents[position]
        return position

    for support, number in zip(supports, multiplicities):
        if not number or len(support) < 2:
            continue
        first = root(support[0])
        for position in support[1:]:
            other = root(position)
            if first != other:
                parents[other] = first
                components -= 1
                if components == 1:
                    return True
    return False


def enumerate_product_theory_candidates(
    gauge_groups: Iterable[str],
    *,
    only_one_sector: bool = True,
) -> list[dict[str, Any]]:
    """Enumerate conformal matter candidates for a product of simple groups.

    Accept a nonempty iterable of Cartan-type strings, e.g. ["A1", "C2"].
    Factors receive IDs gauge_1, gauge_2, ... in input order; repeated Cartan
    types are allowed and factor permutations are not identified. A single
    factor is also accepted, with results in the product-theory input schema.

    Reuse enumerate_product_irreps with inclusive 2*h_dual bounds and its
    simultaneous-conjugation convention. For each total pseudoreal irrep,
    count half hypers with cost T_a(R); otherwise count full hypers with cost
    2*T_a(R). Solve one beta equation per factor using exact row-wise LCM
    scaling and frobenius_system_solve. Odd half-hyper counts are allowed.

    Return dictionaries containing gauge_groups and hypermultiplets, ready
    for the anomaly checker. No anomaly check is performed here. Free
    gauge-singlet matter and zero multiplicities are omitted. Solution order
    is not specified.

    By default, only_one_sector=True retains only connected gauge sectors.
    A lightweight connectivity check uses nonzero Dynkin labels and positive
    multiplicities, before constructing candidate dictionaries or running any
    anomaly/property checks. Set only_one_sector=False to include decoupled
    matter contents as well. This filter does not reduce beta-system solving.
    """
    if isinstance(gauge_groups, (str, bytes)):
        raise ValueError("gauge_groups must be a nonempty iterable of Cartan strings")
    try:
        cartan_types = tuple(gauge_groups)
    except TypeError as exc:
        raise ValueError(
            "gauge_groups must be a nonempty iterable of Cartan strings"
        ) from exc
    if not cartan_types or any(not isinstance(group, str) for group in cartan_types):
        raise ValueError("gauge_groups must be a nonempty iterable of Cartan strings")

    factors = tuple(
        GaugeFactorData(f"gauge_{i}", get_lie_algebra(cartan_type))
        for i, cartan_type in enumerate(cartan_types, start=1)
    )
    budgets = tuple(2 * factor.algebra.dual_coxeter_number for factor in factors)
    reality_cache: dict[tuple[str, DynkinLabels], str] = {}
    matter = []
    for labels, indices in enumerate_product_irreps(factors):
        realities = []
        for factor in factors:
            key = (factor.algebra.cartan_type, labels[factor.factor_id])
            if key not in reality_cache:
                reality_cache[key] = representation_reality(factor.algebra, key[1])
            realities.append(reality_cache[key])
        # Reality is multiplicative for an external tensor product.
        pseudoreal = (
            "complex" not in realities and realities.count("pseudoreal") % 2 == 1
        )
        kind = "half" if pseudoreal else "full"
        costs = tuple(
            indices[factor.factor_id] * (1 if pseudoreal else 2)
            for factor in factors
        )
        if any(cost > budget for cost, budget in zip(costs, budgets)):
            continue
        matter.append((labels, kind, costs))

    coefficients = []
    targets = []
    for i, budget in enumerate(budgets):
        scale = lcm(*(costs[i].denominator for _, _, costs in matter))
        coefficients.append([
            (costs[i] * scale).numerator for _, _, costs in matter
        ])
        targets.append(budget * scale)

    supports = (
        tuple(tuple(i for i, factor in enumerate(factors)
                    if any(labels[factor.factor_id]))
              for labels, _, _ in matter)
        if only_one_sector else ()
    )

    candidates = []
    for solution in frobenius_system_solve(coefficients, targets):
        if only_one_sector and not _has_single_gauge_sector(len(factors), supports, solution):
            continue
        candidate = {
            "gauge_groups": [
                {"id": factor.factor_id, "algebra": factor.algebra.cartan_type}
                for factor in factors
            ],
            "hypermultiplets": [
                {
                    "representations": {
                        factor.factor_id: list(labels[factor.factor_id])
                        for factor in factors
                    },
                    "number": number,
                    "kind": kind,
                }
                for (labels, kind, _), number in zip(matter, solution)
                if number
            ],
        }
        candidates.append(candidate)
    return candidates
