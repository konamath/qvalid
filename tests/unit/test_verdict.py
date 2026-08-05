"""Tests for the verdict of section 7 of ``02``.

``TestAbsenceNeverBecomesAPlace`` is the criterion ``05`` v0.9 states and the
rule the whole module exists to enforce: a strategy whose required evidence did
not run is not ranked, however good its distribution looks. The test that
matters puts the **best** distribution in the unrankable list.

``TestExactProperties`` pins the cumulative prospect theory implementation
against cases with closed answers. A framework that does not contain the neutral
case as an exact special case is probably not computing what it claims.
"""

from __future__ import annotations

import numpy as np
import pytest

from qvalid.core.verdict import (
    DEFAULT_RANKING_REQUIREMENTS,
    Candidate,
    CptParameters,
    Verdict,
    certainty_equivalent,
    cpt_value,
    decision_weights,
    probability_weight,
    rank,
)
from qvalid.exceptions import InsufficientSampleError

NEUTRAL = CptParameters(alpha=1.0, beta=1.0, loss_aversion=1.0, gamma=1.0, delta=1.0)
COMPLETE = ("resampling", "deflated_sharpe")


def candidate(name: str, outcomes: np.ndarray, *, complete: bool = True) -> Candidate:
    if complete:
        return Candidate(name, outcomes, COMPLETE)
    return Candidate(
        name,
        outcomes,
        ("resampling",),
        {"deflated_sharpe": "NOT_REQUESTED: the number of trials was not declared"},
    )


class TestExactProperties:
    def test_neutral_parameters_reduce_to_the_arithmetic_mean(self) -> None:
        """Exact, not approximate. The neutral case is the sanity check on the rest."""
        for draw in (
            np.array([-3.0, -1.0, 0.5, 2.0, 7.0]),
            np.random.default_rng(1).normal(0.02, 0.3, 500),
            np.random.default_rng(2).standard_t(4, 500) * 0.1,
        ):
            assert cpt_value(draw, NEUTRAL) == pytest.approx(float(draw.mean()), rel=1e-12)
            assert certainty_equivalent(draw, NEUTRAL) == pytest.approx(
                float(draw.mean()), rel=1e-12
            )

    @pytest.mark.parametrize("outcome", [-4.0, -0.5, 0.0, 3.5, 100.0])
    def test_a_certain_outcome_is_its_own_certainty_equivalent(self, outcome: float) -> None:
        """The case that first came out ``nan``, before the cumulative probability was clipped."""
        assert certainty_equivalent(np.full(200, outcome)) == pytest.approx(outcome, rel=1e-10)

    def test_a_symmetric_gamble_is_worth_less_than_nothing(self) -> None:
        """Loss aversion, which is the reason not to rank by the mean."""
        equivalent = certainty_equivalent(np.array([-1.0, 1.0]))
        assert equivalent < 0.0
        assert certainty_equivalent(np.array([-1.0, 1.0]), NEUTRAL) == pytest.approx(0.0)

    def test_loss_aversion_of_one_removes_the_asymmetry(self) -> None:
        symmetric = CptParameters(loss_aversion=1.0, alpha=1.0, beta=1.0)
        assert certainty_equivalent(np.array([-1.0, 1.0]), symmetric) == pytest.approx(
            0.0, abs=0.05
        )

    def test_the_weighting_function_fixes_the_endpoints(self) -> None:
        assert float(probability_weight(0.0, 0.61)) == 0.0
        assert float(probability_weight(1.0, 0.61)) == pytest.approx(1.0)

    def test_a_curvature_of_one_is_the_identity(self) -> None:
        grid = np.linspace(0.0, 1.0, 11)
        np.testing.assert_allclose(probability_weight(grid, 1.0), grid)

    def test_small_probabilities_are_overweighted(self) -> None:
        """The feature: a one per cent chance of ruin is not discounted at one per cent."""
        for small in (0.001, 0.01, 0.05, 0.1):
            assert float(probability_weight(small, 0.61)) > small
        for large in (0.6, 0.9, 0.99):
            assert float(probability_weight(large, 0.61)) < large

    def test_a_cumulative_probability_past_one_does_not_produce_nan(self) -> None:
        """Summing equal weights overshoots one by epsilon; a negative base is ``nan``."""
        assert np.isfinite(float(probability_weight(1.0 + 1e-15, 0.61)))
        assert np.isfinite(float(probability_weight(-1e-15, 0.61)))

    def test_the_weights_do_not_sum_to_one_and_that_is_correct(self) -> None:
        """Under a subadditive weighting the gap is the certainty effect, not a bug."""
        outcomes = np.sort(np.linspace(-2.0, 3.0, 1_000))
        total = float(decision_weights(outcomes, CptParameters()).sum())
        assert 0.8 < total < 1.0
        neutral_total = float(decision_weights(outcomes, NEUTRAL).sum())
        assert neutral_total == pytest.approx(1.0, rel=1e-12)

    def test_the_reference_point_shifts_the_answer_and_is_declared(self) -> None:
        outcomes = np.random.default_rng(3).normal(0.05, 0.2, 500)
        at_zero = certainty_equivalent(outcomes, CptParameters())
        at_risk_free = certainty_equivalent(outcomes, CptParameters(reference=0.04))
        assert at_zero != at_risk_free


class TestInvariances:
    def test_first_order_stochastic_dominance_is_respected(self) -> None:
        base = np.random.default_rng(5).normal(0.0, 1.0, 4_000)
        equivalents = [certainty_equivalent(base + shift) for shift in (-0.5, 0.0, 0.5, 1.0)]
        assert equivalents == sorted(equivalents)

    def test_scaling_every_outcome_scales_the_certainty_equivalent(self) -> None:
        """With equal curvature on both sides the value function is homogeneous."""
        outcomes = np.random.default_rng(7).normal(0.0, 1.0, 2_000)
        params = CptParameters(alpha=0.88, beta=0.88)
        base = certainty_equivalent(outcomes, params)
        scaled = certainty_equivalent(outcomes * 3.0, params)
        assert scaled == pytest.approx(3.0 * base, rel=1e-9)

    def test_the_order_of_the_sample_does_not_matter(self) -> None:
        outcomes = np.random.default_rng(11).normal(0.02, 0.3, 1_000)
        shuffled = np.random.default_rng(13).permutation(outcomes)
        assert certainty_equivalent(outcomes) == pytest.approx(certainty_equivalent(shuffled))

    def test_the_certainty_equivalent_is_deterministic(self) -> None:
        outcomes = np.random.default_rng(17).normal(0.02, 0.3, 500)
        assert certainty_equivalent(outcomes) == certainty_equivalent(outcomes)

    def test_more_loss_aversion_can_only_lower_the_certainty_equivalent(self) -> None:
        outcomes = np.random.default_rng(19).normal(0.02, 0.3, 2_000)
        equivalents = [
            certainty_equivalent(outcomes, CptParameters(loss_aversion=lam))
            for lam in (1.0, 2.25, 4.0)
        ]
        assert equivalents == sorted(equivalents, reverse=True)


class TestAbsenceNeverBecomesAPlace:
    """``02`` section 7 and the criterion of ``05`` v0.9."""

    def test_the_best_distribution_is_not_ranked_when_its_evidence_is_missing(self) -> None:
        """The test that matters. A better mean does not buy a place in the ordering."""
        rng = np.random.default_rng(23)
        ranking = rank(
            [
                candidate("corrected", rng.normal(0.05, 0.2, 2_000)),
                candidate("uncorrected", rng.normal(0.30, 0.2, 2_000), complete=False),
                candidate("corrected_worse", rng.normal(0.01, 0.2, 2_000)),
            ]
        )
        assert [v.name for v in ranking.ranked] == ["corrected", "corrected_worse"]
        assert [v.name for v in ranking.unrankable] == ["uncorrected"]
        assert ranking.best is not None
        assert ranking.best.name == "corrected"

    def test_an_unrankable_candidate_has_no_certainty_equivalent_at_all(self) -> None:
        """Computing one and hiding it would leave the number available to anyone."""
        ranking = rank([candidate("x", np.array([0.5, 0.6, 0.7]), complete=False)])
        verdict = ranking.unrankable[0]
        assert verdict.certainty_equivalent is None
        assert verdict.cpt_value is None
        assert verdict.rankable is False

    def test_the_blocking_sections_and_the_reason_are_carried(self) -> None:
        ranking = rank([candidate("x", np.array([0.5, 0.6]), complete=False)])
        verdict = ranking.unrankable[0]
        assert verdict.blocking_sections == ("deflated_sharpe",)
        assert "not comparable" in verdict.reason
        assert "the number of trials was not declared" in verdict.reason

    def test_a_section_absent_from_the_panel_entirely_still_blocks(self) -> None:
        """Missing from the panel is a pipeline bug; missing from the run is declared absence.
        Both block the ranking, and the message says which it was.
        """
        ranking = rank([Candidate("x", np.array([0.5, 0.6]), ("resampling",))])
        assert "not present in the panel" in ranking.unrankable[0].reason

    def test_the_two_lists_are_never_interleaved(self) -> None:
        rng = np.random.default_rng(29)
        ranking = rank(
            [
                candidate(f"c{i}", rng.normal(0.1 * i, 0.2, 500), complete=i % 2 == 0)
                for i in range(6)
            ]
        )
        assert len(ranking.ranked) == 3
        assert len(ranking.unrankable) == 3
        assert not set(v.name for v in ranking.ranked) & set(v.name for v in ranking.unrankable)

    def test_the_requirements_are_declared_on_the_result(self) -> None:
        ranking = rank([candidate("x", np.array([0.5, 0.6]))])
        assert ranking.requirements == DEFAULT_RANKING_REQUIREMENTS
        assert ranking.parameters.alpha == 0.88

    def test_the_requirements_can_be_widened(self) -> None:
        ranking = rank(
            [candidate("x", np.array([0.5, 0.6]))],
            requirements=("resampling", "deflated_sharpe", "regimes"),
        )
        assert ranking.ranked == ()
        assert ranking.unrankable[0].blocking_sections == ("regimes",)

    def test_no_requirement_ranks_everything(self) -> None:
        ranking = rank([candidate("x", np.array([0.5, 0.6]), complete=False)], requirements=())
        assert len(ranking.ranked) == 1

    def test_nothing_rankable_leaves_no_best(self) -> None:
        ranking = rank([candidate("x", np.array([0.5, 0.6]), complete=False)])
        assert ranking.best is None

    def test_the_verdict_type_refuses_an_inconsistent_state(self) -> None:
        """A number without a qualifying panel is not representable."""
        with pytest.raises(ValueError, match="inconsistent"):
            Verdict(
                name="x",
                certainty_equivalent=1.0,
                cpt_value=1.0,
                rankable=False,
                blocking_sections=(),
                reason="why",
                parameters=CptParameters(),
            )
        with pytest.raises(ValueError, match="inconsistent"):
            Verdict(
                name="x",
                certainty_equivalent=None,
                cpt_value=None,
                rankable=True,
                blocking_sections=(),
                reason=None,
                parameters=CptParameters(),
            )

    def test_an_unrankable_verdict_must_state_why(self) -> None:
        with pytest.raises(ValueError, match="states no reason"):
            Verdict(
                name="x",
                certainty_equivalent=None,
                cpt_value=None,
                rankable=False,
                blocking_sections=("a",),
                reason=None,
                parameters=CptParameters(),
            )


class TestReproducibility:
    """``05`` v0.9: the ordering is reproducible from the declared parameters."""

    def test_the_same_parameters_give_the_same_order(self) -> None:
        rng = np.random.default_rng(31)
        pool = [candidate(f"c{i}", rng.normal(0.02 * i, 0.25, 800)) for i in range(5)]
        first = rank(pool, params=CptParameters(gamma=0.5))
        second = rank(pool, params=CptParameters(gamma=0.5))
        assert [v.name for v in first.ranked] == [v.name for v in second.ranked]

    def test_different_preferences_can_reorder(self) -> None:
        """If the order is stable across preferences, it was never about preferences."""
        rng = np.random.default_rng(37)
        steady = rng.normal(0.03, 0.05, 3_000)
        lumpy = rng.normal(0.06, 0.40, 3_000)
        pool = [candidate("steady", steady), candidate("lumpy", lumpy)]
        tolerant = rank(pool, params=CptParameters(loss_aversion=1.0)).ranked[0].name
        averse = rank(pool, params=CptParameters(loss_aversion=4.0)).ranked[0].name
        assert tolerant == "lumpy"
        assert averse == "steady"

    def test_every_verdict_carries_the_parameters_that_produced_it(self) -> None:
        params = CptParameters(alpha=0.7, loss_aversion=3.0)
        ranking = rank([candidate("x", np.array([0.1, -0.2, 0.3]))], params=params)
        assert ranking.ranked[0].parameters == params

    def test_ties_break_by_name_so_the_order_is_deterministic(self) -> None:
        same = np.array([0.1, -0.2, 0.3])
        ranking = rank([candidate("zebra", same), candidate("alpha", same)])
        assert [v.name for v in ranking.ranked] == ["alpha", "zebra"]

    def test_the_serialisation_carries_both_lists_and_the_parameters(self) -> None:
        rng = np.random.default_rng(41)
        ranking = rank(
            [
                candidate("good", rng.normal(0.05, 0.2, 500)),
                candidate("blocked", rng.normal(0.5, 0.2, 500), complete=False),
            ]
        )
        payload = ranking.to_dict()
        assert [entry["name"] for entry in payload["ranked"]] == ["good"]
        assert payload["unrankable"][0]["blocking_sections"] == ["deflated_sharpe"]
        assert payload["parameters"]["loss_aversion"] == 2.25
        assert payload["requirements"] == list(DEFAULT_RANKING_REQUIREMENTS)


class TestDegenerateCases:
    def test_an_empty_sample_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least one outcome"):
            certainty_equivalent(np.array([], dtype=np.float64))

    def test_a_non_finite_outcome_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="must be finite"):
            certainty_equivalent(np.array([0.1, np.inf, 0.2]))
        with pytest.raises(InsufficientSampleError, match="must be finite"):
            certainty_equivalent(np.array([0.1, np.nan]))

    def test_an_all_loss_sample_gives_a_negative_equivalent(self) -> None:
        assert certainty_equivalent(np.full(100, -0.2)) == pytest.approx(-0.2, rel=1e-10)

    def test_an_all_gain_sample_gives_a_positive_equivalent(self) -> None:
        outcomes = np.random.default_rng(43).uniform(0.1, 0.5, 500)
        assert 0.1 <= certainty_equivalent(outcomes) <= 0.5

    def test_a_single_outcome_works(self) -> None:
        assert certainty_equivalent(np.array([0.25])) == pytest.approx(0.25, rel=1e-10)

    def test_an_empty_candidate_list_ranks_to_nothing(self) -> None:
        ranking = rank([])
        assert ranking.ranked == ()
        assert ranking.unrankable == ()
        assert ranking.best is None

    def test_impossible_parameters_are_refused(self) -> None:
        for kwargs in (
            {"alpha": 0.0},
            {"alpha": 1.5},
            {"gamma": 0.0},
            {"delta": 1.5},
            {"loss_aversion": 0.5},
        ):
            with pytest.raises(ValueError):
                CptParameters(**kwargs)

    def test_the_neutral_flag_matches_the_parameters(self) -> None:
        assert NEUTRAL.is_neutral is True
        assert CptParameters().is_neutral is False
        assert CptParameters(alpha=1.0).is_neutral is False
