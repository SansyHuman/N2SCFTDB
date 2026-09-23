from fractions import Fraction
from itertools import product
import json
from math import prod
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from anomalies.check_n2_anomalies import (
    GaugeFactorData,
    check_input_data,
    check_product_theory,
)
from anomalies.lie_algebra import get_lie_algebra, representation_reality
from common.n2_theory_iter import (
    _has_single_gauge_sector,
    enumerate_irreps,
    enumerate_product_irreps,
    enumerate_product_theory_candidates,
    enumerate_simple_theory_candidates,
)


def factor(factor_id, algebra="A1"):
    return GaugeFactorData(factor_id, get_lie_algebra(algebra))


class IrrepEnumerationTests(unittest.TestCase):
    def test_default_bound_includes_half_hyper_at_twice_adjoint_index(self):
        gauge_factor = factor("g", "C2")
        # Sp(2): h_dual = 3; the pseudoreal 16 has T = 6.
        candidate = ((1, 1), Fraction(6))
        self.assertIn(candidate, enumerate_irreps(gauge_factor))
        self.assertNotIn(
            candidate, enumerate_irreps(gauge_factor, inclusive=False)
        )
        self.assertNotIn(
            candidate, enumerate_irreps(gauge_factor, max_index=3)
        )

    def test_su3_prefers_fundamental_over_antifundamental_by_default(self):
        self.assertEqual(
            enumerate_irreps(factor("g", "A2")),
            [
                ((1, 0), Fraction(1, 2)),
                ((1, 1), Fraction(3)),
                ((2, 0), Fraction(5, 2)),
            ],
        )

    def test_su3_includes_conjugates_and_adjoint_in_label_order(self):
        self.assertEqual(
            enumerate_irreps(factor("g", "A2"), identify_conjugates=False),
            [
                ((0, 1), Fraction(1, 2)),
                ((0, 2), Fraction(5, 2)),
                ((1, 0), Fraction(1, 2)),
                ((1, 1), Fraction(3)),
                ((2, 0), Fraction(5, 2)),
            ],
        )

    def test_strict_cutoff_and_optional_singlet(self):
        gauge_factor = factor("g")
        self.assertEqual(
            enumerate_irreps(gauge_factor, max_index=2, inclusive=False),
            [((1,), Fraction(1, 2))],
        )
        self.assertEqual(
            enumerate_irreps(gauge_factor, include_singlet=True),
            [((0,), Fraction(0)), ((1,), Fraction(1, 2)), ((2,), Fraction(2))],
        )

    def test_fractional_bound(self):
        gauge_factor = factor("g")
        self.assertEqual(
            enumerate_irreps(gauge_factor, max_index=Fraction(1, 2)),
            [((1,), Fraction(1, 2))],
        )
        self.assertEqual(
            enumerate_irreps(
                gauge_factor, max_index=Fraction(1, 2), inclusive=False
            ),
            [],
        )

    def test_e8_has_only_adjoint_at_default_bound(self):
        gauge_factor = factor("g", "E8")
        self.assertEqual(
            enumerate_irreps(gauge_factor, max_index=30, inclusive=False), []
        )
        self.assertEqual(
            enumerate_irreps(gauge_factor, inclusive=False),
            [(gauge_factor.algebra.adjoint_labels, Fraction(30))],
        )
        self.assertEqual(
            enumerate_irreps(gauge_factor),
            [(gauge_factor.algebra.adjoint_labels, Fraction(30))],
        )

    def test_invalid_bounds_are_rejected(self):
        gauge_factor = factor("g")
        for bound in (0, -1, 0.5, True, "1.0"):
            with self.subTest(bound=bound):
                with self.assertRaises(ValueError):
                    enumerate_irreps(gauge_factor, max_index=bound)
                with self.assertRaises(ValueError):
                    enumerate_product_irreps(
                        [gauge_factor], max_indices={"g": bound}
                    )


class ProductIrrepEnumerationTests(unittest.TestCase):
    def test_su3_pair_identifies_only_simultaneous_conjugates(self):
        factors = [factor("left", "A2"), factor("right", "A2")]
        rows = enumerate_product_irreps(factors)
        self.assertEqual(
            [(tuple(labels.values()), tuple(indices.values()))
             for labels, indices in rows],
            [
                (((0, 0), (1, 0)), (Fraction(0), Fraction(1, 2))),
                (((0, 0), (1, 1)), (Fraction(0), Fraction(3))),
                (((0, 0), (2, 0)), (Fraction(0), Fraction(5, 2))),
                (((1, 0), (0, 0)), (Fraction(1, 2), Fraction(0))),
                (((1, 0), (0, 1)), (Fraction(3, 2), Fraction(3, 2))),
                (((1, 0), (1, 0)), (Fraction(3, 2), Fraction(3, 2))),
                (((1, 1), (0, 0)), (Fraction(3), Fraction(0))),
                (((2, 0), (0, 0)), (Fraction(5, 2), Fraction(0))),
            ],
        )

        all_rows = enumerate_product_irreps(factors, identify_conjugates=False)
        self.assertEqual(len(all_rows), 14)
        all_labels = [tuple(labels.values()) for labels, _ in all_rows]
        for bifundamental in product(((0, 1), (1, 0)), repeat=2):
            self.assertIn(bifundamental, all_labels)

    def test_su2_pair_keeps_partial_singlets_and_filters_spectator_indices(self):
        self.assertEqual(
            enumerate_product_irreps([factor("left"), factor("right")]),
            [
                ({"left": (0,), "right": (1,)},
                 {"left": Fraction(0), "right": Fraction(1, 2)}),
                ({"left": (0,), "right": (2,)},
                 {"left": Fraction(0), "right": Fraction(2)}),
                ({"left": (1,), "right": (0,)},
                 {"left": Fraction(1, 2), "right": Fraction(0)}),
                ({"left": (1,), "right": (1,)},
                 {"left": Fraction(1), "right": Fraction(1)}),
                ({"left": (1,), "right": (2,)},
                 {"left": Fraction(3, 2), "right": Fraction(4)}),
                ({"left": (2,), "right": (0,)},
                 {"left": Fraction(2), "right": Fraction(0)}),
                ({"left": (2,), "right": (1,)},
                 {"left": Fraction(4), "right": Fraction(3, 2)}),
            ],
        )

    def test_three_factors_match_independent_su2_formula(self):
        factors = [factor(name) for name in ("a", "b", "c")]
        bounds = {"a": Fraction(2), "b": Fraction(3), "c": Fraction(4)}
        for inclusive, include_singlet in product((False, True), repeat=2):
            with self.subTest(inclusive=inclusive, include_singlet=include_singlet):
                expected = []
                # SU(2): dim([n]) = n+1, T([n]) = n(n+1)(n+2)/12.
                # n >= 3 has T >= 5, above every bound used here.
                for labels in product(range(4), repeat=3):
                    indices = tuple(
                        Fraction(n * (n + 1) * (n + 2), 12)
                        * prod(labels[j] + 1 for j in range(3) if j != i)
                        for i, n in enumerate(labels)
                    )
                    allowed = all(
                        value <= bound if inclusive else value < bound
                        for value, bound in zip(indices, bounds.values())
                    )
                    if allowed and (include_singlet or any(labels)):
                        expected.append((tuple((n,) for n in labels), indices))
                actual = enumerate_product_irreps(
                    factors,
                    max_indices=bounds,
                    inclusive=inclusive,
                    include_singlet=include_singlet,
                )
                self.assertEqual(
                    [(tuple(labels.values()), tuple(indices.values()))
                     for labels, indices in actual],
                    expected,
                )

    def test_bound_override_retains_defaults_for_other_factors(self):
        factors = [factor("left"), factor("right", "A2")]
        # (2, 8) saturates the doubled bounds (4, 6) for SU(2) x SU(3).
        target = (
            {"left": (1,), "right": (1, 1)},
            {"left": Fraction(4), "right": Fraction(6)},
        )
        self.assertIn(target, enumerate_product_irreps(factors))
        self.assertIn(
            target, enumerate_product_irreps(factors, max_indices={"left": 4})
        )
        self.assertNotIn(
            target, enumerate_product_irreps(factors, max_indices={"left": 2})
        )
        self.assertNotIn(
            target, enumerate_product_irreps(factors, max_indices={"right": 3})
        )
        self.assertNotIn(
            target,
            enumerate_product_irreps(
                factors, max_indices={"left": 4}, inclusive=False
            ),
        )

    def test_mixed_rank_full_hyper_bound_agrees_with_anomaly_checker(self):
        factors = [factor("left"), factor("right", "A2")]
        rows = enumerate_product_irreps(
            factors,
            max_indices={
                item.factor_id: item.algebra.dual_coxeter_number
                for item in factors
            },
        )
        self.assertIn(
            ({"left": (1,), "right": (1, 0)},
             {"left": Fraction(3, 2), "right": Fraction(1)}),
            rows,
        )
        gauge_groups = [
            {"id": item.factor_id, "algebra": item.algebra.cartan_type}
            for item in factors
        ]
        for labels, indices in rows:
            checked = check_product_theory(
                gauge_groups,
                [{"representations": {key: list(value) for key, value in labels.items()}}],
            )
            self.assertFalse(checked["errors"])
            for item in checked["gauge_factors"]:
                self.assertEqual(
                    item["matter_beta_contribution"], 2 * indices[item["id"]]
                )
                self.assertGreaterEqual(item["b0"], 0)

    def test_single_factor_iterator_matches_simple_enumeration(self):
        gauge_factor = factor("g", "A2")
        for inclusive, include_singlet, identify_conjugates in product(
            (False, True), repeat=3
        ):
            with self.subTest(
                inclusive=inclusive,
                include_singlet=include_singlet,
                identify_conjugates=identify_conjugates,
            ):
                rows = enumerate_product_irreps(
                    iter([gauge_factor]),
                    inclusive=inclusive,
                    include_singlet=include_singlet,
                    identify_conjugates=identify_conjugates,
                )
                self.assertEqual(
                    [(labels["g"], indices["g"]) for labels, indices in rows],
                    enumerate_irreps(
                        gauge_factor,
                        inclusive=inclusive,
                        include_singlet=include_singlet,
                        identify_conjugates=identify_conjugates,
                    ),
                )

    def test_factor_permutation_preserves_representations_and_bounds_by_id(self):
        factors = [factor("left"), factor("right", "A2")]
        options = {"max_indices": {"left": Fraction(3, 2)}}
        forward = enumerate_product_irreps(factors, **options)
        backward = enumerate_product_irreps(reversed(factors), **options)
        self.assertCountEqual(forward, backward)
        for labels, indices in backward:
            self.assertEqual(list(labels), ["right", "left"])
            self.assertEqual(list(indices), ["right", "left"])

    def test_invalid_factors_and_bound_mappings_are_rejected(self):
        gauge_factor = factor("g")
        for factors in ([], None, ["A1"], [gauge_factor, gauge_factor],
                        [factor("")], [factor(1)]):
            with self.subTest(factors=factors):
                with self.assertRaises(ValueError):
                    enumerate_product_irreps(factors)
        for bounds in ([2], {"unknown": 2}):
            with self.subTest(bounds=bounds):
                with self.assertRaises(ValueError):
                    enumerate_product_irreps([gauge_factor], max_indices=bounds)


class SimpleTheoryCandidateTests(unittest.TestCase):
    @staticmethod
    def matter_key(candidate):
        """Compare matter contents independently of solver and hyper order."""
        return tuple(sorted(
            (tuple(hyper["dynkin_labels"]), hyper["kind"], hyper["number"])
            for hyper in candidate["hypermultiplets"]
        ))

    def assert_matter_contents(self, cartan_type, expected):
        candidates = enumerate_simple_theory_candidates(cartan_type)
        for candidate in candidates:
            self.assertEqual(candidate["algebra"], cartan_type)
        self.assertCountEqual(
            [self.matter_key(candidate) for candidate in candidates], expected
        )

    def test_su2_has_eight_half_fundamentals_or_one_full_adjoint(self):
        self.assert_matter_contents("A1", [
            (((1,), "half", 8),),
            (((2,), "full", 1),),
        ])

    def test_su3_has_exactly_three_full_hyper_matter_contents(self):
        # T(3) = 1/2, T(6) = 5/2, T(8) = 3; h_dual = 3.
        self.assert_matter_contents("A2", [
            (((1, 0), "full", 6),),
            (((1, 0), "full", 1), ((2, 0), "full", 1)),
            (((1, 1), "full", 1),),
        ])

    def test_a32_has_exactly_six_full_hyper_matter_contents(self):
        # SU(33): n_fund + 31*n_antisym + 35*n_sym + 66*n_adj = 66.
        fundamental = (1,) + (0,) * 31
        antisymmetric = (0, 1) + (0,) * 30
        symmetric = (2,) + (0,) * 31
        adjoint = (1,) + (0,) * 30 + (1,)
        self.assert_matter_contents("A32", [
            ((fundamental, "full", 66),),
            ((antisymmetric, "full", 1), (fundamental, "full", 35)),
            ((antisymmetric, "full", 2), (fundamental, "full", 4)),
            ((fundamental, "full", 31), (symmetric, "full", 1)),
            ((antisymmetric, "full", 1), (symmetric, "full", 1)),
            ((adjoint, "full", 1),),
        ])

    def test_sp2_fractional_indices_and_half_hyper_at_upper_bound(self):
        # h_dual = 3: T(4) = 1/2, T(5) = 1, T(10) = 3, T(16) = 6.
        # The 4 and 16 are pseudoreal; the 5 and adjoint 10 are real.
        self.assert_matter_contents("C2", [
            (((1, 0), "half", 12),),
            (((0, 1), "full", 1), ((1, 0), "half", 8)),
            (((0, 1), "full", 2), ((1, 0), "half", 4)),
            (((0, 1), "full", 3),),
            (((1, 1), "half", 1),),
            (((2, 0), "full", 1),),
        ])

    def test_sp4_keeps_large_half_hyper_and_excludes_unusable_full_hyper(self):
        # h_dual = 5: the pseudoreal 48 has T = 7 and is usable as a half
        # hyper. The real 42 also has T = 7, so a full hyper exceeds 2*h_dual.
        self.assert_matter_contents("C4", [
            (((1, 0, 0, 0), "half", 20),),
            (((0, 1, 0, 0), "full", 1), ((1, 0, 0, 0), "half", 8)),
            (((0, 0, 1, 0), "half", 1), ((1, 0, 0, 0), "half", 6)),
            (((2, 0, 0, 0), "full", 1),),
        ])

    def test_candidates_match_exhaustive_rational_reference_across_families(self):
        for cartan_type in (
            "A1", "A2", "A4", "A5", "B3", "C2", "C3", "C4",
            "D4", "D5", "E6", "E7", "E8", "F4", "G2",
        ):
            with self.subTest(cartan_type=cartan_type):
                gauge_factor = factor("gauge", cartan_type)
                irreps = enumerate_irreps(gauge_factor)
                kinds = {
                    labels: "half" if representation_reality(
                        gauge_factor.algebra, labels
                    ) == "pseudoreal" else "full"
                    for labels, _ in irreps
                }
                costs = tuple(
                    index * (1 if kinds[labels] == "half" else 2)
                    for labels, index in irreps
                )
                budget = Fraction(2 * gauge_factor.algebra.dual_coxeter_number)

                # Independent reference: enumerate finite multiplicity ranges
                # and add exact rational costs, without CP-SAT or LCM scaling.
                expected = {
                    counts
                    for counts in product(*(
                        range(int(budget // cost) + 1) for cost in costs
                    ))
                    if sum((cost * count for cost, count in zip(costs, counts)),
                           Fraction(0)) == budget
                }

                candidates = enumerate_simple_theory_candidates(cartan_type)
                actual = []
                for candidate in candidates:
                    self.assertEqual(candidate["algebra"], cartan_type)
                    counts_by_labels = {}
                    for hyper in candidate["hypermultiplets"]:
                        labels = tuple(hyper["dynkin_labels"])
                        self.assertNotIn(labels, counts_by_labels)
                        self.assertIn(labels, kinds)
                        self.assertTrue(any(labels))  # No free gauge singlets.
                        self.assertIs(type(hyper["number"]), int)
                        self.assertGreater(hyper["number"], 0)
                        self.assertEqual(hyper["kind"], kinds[labels])
                        counts_by_labels[labels] = hyper["number"]
                    actual.append(tuple(
                        counts_by_labels.get(labels, 0) for labels, _ in irreps
                    ))

                    # Exercise the public input format after JSON serialization.
                    checked = check_input_data(json.loads(json.dumps(candidate)))
                    self.assertEqual(checked["errors"], [])
                    self.assertEqual(checked["b0"], 0)
                    self.assertTrue(checked["one_loop_beta_vanishes"])

                self.assertEqual(len(actual), len(set(actual)))
                self.assertEqual(set(actual), expected)


class ProductTheoryCandidateTests(unittest.TestCase):
    @staticmethod
    def matter_key(candidate):
        ids = [factor["id"] for factor in candidate["gauge_groups"]]
        return tuple(sorted(
            (tuple(tuple(hyper["representations"][key]) for key in ids),
             hyper["kind"], hyper["number"])
            for hyper in candidate["hypermultiplets"]
        ))

    def assert_valid_candidates(self, candidates):
        keys = []
        for candidate in candidates:
            keys.append(self.matter_key(candidate))
            checked = check_input_data(candidate)
            self.assertEqual(checked["errors"], [])
            self.assertTrue(checked["one_loop_beta_vanishes"])
            self.assertTrue(checked["anomaly_free"])
            for hyper, parsed in zip(candidate["hypermultiplets"], checked["hypermultiplets"]):
                self.assertIs(type(hyper["number"]), int)
                self.assertGreater(hyper["number"], 0)
                self.assertTrue(any(any(labels) for labels in hyper["representations"].values()))
                self.assertEqual(
                    hyper["kind"], "half" if parsed.reality == "pseudoreal" else "full"
                )
        self.assertEqual(len(keys), len(set(keys)))
        return set(keys)

    def test_su2_pair_has_exactly_eight_matter_contents(self):
        candidates = enumerate_product_theory_candidates(["A1", "A1"], only_one_sector=False)
        actual = self.assert_valid_candidates(candidates)

        def matter(*terms):
            return tuple(sorted(
                (((left,), (right,)), kind, number)
                for left, right, kind, number in terms
            ))

        expected = {
            matter((2, 0, "full", 1), (0, 2, "full", 1)),
            matter((1, 0, "half", 8), (0, 2, "full", 1)),
            matter((2, 0, "full", 1), (0, 1, "half", 8)),
            matter((1, 0, "half", 8), (0, 1, "half", 8)),
            matter((1, 1, "full", 2)),
            matter((1, 1, "full", 1), (1, 0, "half", 4), (0, 1, "half", 4)),
            matter((1, 2, "half", 1), (1, 0, "half", 5)),
            matter((2, 1, "half", 1), (0, 1, "half", 5)),
        }
        self.assertEqual(actual, expected)

    def test_single_factor_agrees_with_simple_enumeration(self):
        for algebra in ("A1", "A2", "C2", "C4", "G2", "E6"):
            with self.subTest(algebra=algebra):
                candidates = enumerate_product_theory_candidates([algebra])
                self.assert_valid_candidates(candidates)
                self.assertCountEqual(
                    [tuple((labels[0], kind, number)
                           for labels, kind, number in self.matter_key(candidate))
                     for candidate in candidates],
                    [SimpleTheoryCandidateTests.matter_key(candidate)
                     for candidate in enumerate_simple_theory_candidates(algebra)],
                )

    def test_su3_pair_keeps_both_bifundamental_orientations(self):
        candidates = enumerate_product_theory_candidates(["A2", "A2"])
        keys = self.assert_valid_candidates(candidates)
        self.assertIn(((((1, 0), (1, 0)), "full", 2),), keys)
        self.assertIn(((((1, 0), (0, 1)), "full", 2),), keys)
        self.assertIn(tuple(sorted([
            (((1, 0), (1, 0)), "full", 1),
            (((1, 0), (0, 1)), "full", 1),
        ])), keys)
        self.assertNotIn(((((0, 1), (0, 1)), "full", 2),), keys)

    def test_three_pseudoreal_factors_allow_odd_trifundamental_count(self):
        candidates = enumerate_product_theory_candidates(["A1"] * 3)
        keys = self.assert_valid_candidates(candidates)
        self.assertIn(((((1,), (1,), (1,)), "half", 2),), keys)
        self.assertIn(tuple(sorted([
            (((1,), (1,), (1,)), "half", 1),
            (((1,), (0,), (0,)), "half", 4),
            (((0,), (1,), (0,)), "half", 4),
            (((0,), (0,), (1,)), "half", 4),
        ])), keys)

    def test_mixed_groups_match_exhaustive_rational_reference(self):
        for algebras in (("A1", "A2"), ("A1", "C2"), ("C2", "G2")):
            with self.subTest(algebras=algebras):
                factors = [factor(f"gauge_{i}", algebra)
                           for i, algebra in enumerate(algebras, start=1)]
                groups = [{"id": f.factor_id, "algebra": f.algebra.cartan_type}
                          for f in factors]
                budgets = [2 * f.algebra.dual_coxeter_number for f in factors]
                types, costs, bounds = [], [], []
                for labels, _ in enumerate_product_irreps(factors):
                    # Derive costs through the checker, independently of the
                    # enumerator's reality classification and integer scaling.
                    data = {"gauge_groups": groups, "hypermultiplets": [{
                        "representations": {key: list(value) for key, value in labels.items()},
                        "kind": "full", "number": 1,
                    }]}
                    checked = check_input_data(data)
                    self.assertEqual(checked["errors"], [])
                    hyper = checked["hypermultiplets"][0]
                    half = hyper.reality == "pseudoreal"
                    cost = tuple(hyper.beta_contributions[f.factor_id] / (2 if half else 1)
                                 for f in factors)
                    types.append((tuple(labels[f.factor_id] for f in factors),
                                  "half" if half else "full"))
                    costs.append(cost)
                    bounds.append(min(int(b // c) for b, c in zip(budgets, cost) if c))
                expected = {
                    tuple(sorted((*kind, number) for kind, number in zip(types, counts) if number))
                    for counts in product(*(range(bound + 1) for bound in bounds))
                    if all(sum(number * cost[a] for number, cost in zip(counts, costs)) == budget
                           for a, budget in enumerate(budgets))
                }
                candidates = enumerate_product_theory_candidates(algebras, only_one_sector=False)
                self.assertEqual(self.assert_valid_candidates(candidates), expected)

    def test_iterable_input_factor_ids_and_independent_output_objects(self):
        candidates = enumerate_product_theory_candidates(iter(["a_1", "C2"]))
        expected_groups = [{"id": "gauge_1", "algebra": "A1"},
                           {"id": "gauge_2", "algebra": "C2"}]
        self.assertGreater(len(candidates), 1)
        for candidate in candidates:
            self.assertEqual(candidate["gauge_groups"], expected_groups)
            self.assertEqual(json.loads(json.dumps(candidate)), candidate)
        before = json.dumps(candidates[1:])
        candidates[0]["gauge_groups"][0]["id"] = "changed"
        candidates[0]["hypermultiplets"][0]["representations"]["gauge_1"][0] = 100
        self.assertEqual(json.dumps(candidates[1:]), before)

    def test_connected_filter_matches_sector_partition_across_product_groups(self):
        from index.n2_theory_index import split_disconnected_sectors

        for algebras, total, connected in (
            (("A1",), 2, 2),
            (("A1", "A1"), 8, 4),
            (("A1", "A1", "A1"), 50, 18),
            (("A1", "A2"), 8, 2),
            (("A2", "A2"), 14, 5),
            (("C2", "G2"), 14, 2),
        ):
            with self.subTest(algebras=algebras):
                all_candidates = enumerate_product_theory_candidates(algebras, only_one_sector=False)
                self.assertEqual(len(all_candidates), total)
                factors = tuple(factor(f"gauge_{i}", algebra)
                                for i, algebra in enumerate(algebras, 1))
                expected = [
                    self.matter_key(candidate) for candidate in all_candidates
                    if len(split_disconnected_sectors(
                        factors, check_input_data(candidate)["hypermultiplets"],
                    )) == 1
                ]
                explicit = enumerate_product_theory_candidates(algebras, only_one_sector=True)
                default = enumerate_product_theory_candidates(algebras)
                self.assertEqual(len(explicit), connected)
                self.assertCountEqual([self.matter_key(c) for c in explicit], expected)
                self.assertCountEqual([self.matter_key(c) for c in default], expected)

    def test_connectivity_filter_does_not_run_anomaly_or_property_checks(self):
        with patch("anomalies.check_n2_anomalies.check_product_theory",
                   side_effect=AssertionError("unexpected anomaly check")), \
             patch("common.n2_theory_properties.calculate_n2_theory_properties",
                   side_effect=AssertionError("unexpected property check")), \
             patch("index.n2_theory_index.split_disconnected_sectors",
                   side_effect=AssertionError("unexpected full sector partition")):
            candidates = enumerate_product_theory_candidates(["A1", "A1"])
        self.assertEqual(len(candidates), 4)

    def test_disabled_filter_skips_connectivity_work(self):
        with patch("common.n2_theory_iter._has_single_gauge_sector",
                   side_effect=AssertionError("unexpected connectivity check")):
            candidates = enumerate_product_theory_candidates(["A1", "A1"], only_one_sector=False)
        self.assertEqual(len(candidates), 8)

    def test_invalid_cartan_iterables_are_rejected(self):
        for groups in ([], None, "A1", b"A1", [1], ["A1", None], ["A0"], ["D2"]):
            with self.subTest(groups=groups):
                with self.assertRaises(ValueError):
                    enumerate_product_theory_candidates(groups)


class GaugeConnectivityTests(unittest.TestCase):
    def test_transitive_links_isolated_factors_and_matter_support(self):
        cases = (
            (1, (), (), True),
            (2, (), (), False),
            (2, ((0,), (1,)), (8, 8), False),
            (2, ((0, 1),), (2,), True),
            (3, ((0, 1),), (1,), False),
            (3, ((1, 2), (0, 1)), (1, 1), True),
            (3, ((0, 1, 2),), (1,), True),
            (3, ((0, 1), (1, 2)), (1, 0), False),
            (4, ((0, 1), (2, 3)), (1, 1), False),
            (4, ((0, 1), (2, 3), (1, 2)), (1, 1, 1), True),
            (4, ((0, 1, 2), (2, 3), (0, 2)), (1, 1, 4), True),
        )
        for count, supports, numbers, expected in cases:
            with self.subTest(count=count, supports=supports, numbers=numbers):
                self.assertEqual(_has_single_gauge_sector(count, supports, numbers), expected)
        chain = tuple((i, i + 1) for i in reversed(range(47)))
        self.assertTrue(_has_single_gauge_sector(48, chain, [1] * len(chain)))


if __name__ == "__main__":
    unittest.main()
