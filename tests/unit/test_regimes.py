"""Tests for the regime labelling and attribution of section 4 of ``02``.

``TestNoLookahead`` is the class ``05`` singles out. It checks causality two
ways, both exact and without tolerance:

- prefix stability, labelling a prefix of the series reproduces the prefix of
  the labels;
- invariance to perturbing the future, changing the series after period ``k``
  leaves every label before ``k`` untouched.

The second is strictly stronger and is what a quantile computed over the whole
sample would fail. The first alone would pass for an estimator that peeks one
period ahead, so both are needed.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import f_oneway

from qvalid.contracts import (
    UNDEFINED_STATE,
    Basis,
    Period,
    PeriodReturns,
    RegimeLabels,
    SchemaError,
)
from qvalid.core.constants import MIN_STATE_OBS
from qvalid.core.regimes import (
    DEFAULT_REGIME_WINDOW,
    DEFAULT_STATES_PER_AXIS,
    MARKOV_RESAMPLE,
    attribute_by_regime,
    expanding_quantile_states,
    label_regimes,
    markov_resample,
    transition_matrix,
    welch_anova,
)
from qvalid.exceptions import InsufficientSampleError, LookaheadError, RegimeSparsityError

DAY_NS = 86_400 * 1_000_000_000
CAPITAL = 100_000.0


def ends(n_periods: int) -> np.ndarray:
    return np.arange(n_periods, dtype=np.int64) * DAY_NS


def periods(values: np.ndarray, *, basis: Basis = Basis.FIXED_INITIAL) -> PeriodReturns:
    values = np.ascontiguousarray(values, dtype=np.float64)
    return PeriodReturns(
        values=values,
        period_end_ns=ends(values.size),
        period=Period.DAILY,
        periods_per_year=260.89,
        calendar_id="TEST",
        basis=basis,
        initial_capital=CAPITAL,
        n_active=int((values != 0.0).sum()),
    )


def reference_series(n_periods: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0003, 0.011, n_periods)


class TestNoLookahead:
    """The criterion ``05`` v0.5 singles out, checked exactly."""

    def test_labelling_a_prefix_reproduces_the_prefix_of_the_labels(self) -> None:
        series = reference_series(1_200, seed=5)
        full = label_regimes(series, ends(1_200), reference_id="REF")
        for cut in (200, 700, 1_199):
            prefix = label_regimes(series[:cut], ends(cut), reference_id="REF")
            np.testing.assert_array_equal(np.asarray(prefix.trend), np.asarray(full.trend)[:cut])
            np.testing.assert_array_equal(
                np.asarray(prefix.volatility), np.asarray(full.volatility)[:cut]
            )

    def test_perturbing_the_future_leaves_the_past_untouched(self) -> None:
        """The strong form. A quantile over the whole sample fails this."""
        series = reference_series(1_200, seed=7)
        base = label_regimes(series, ends(1_200), reference_id="REF")
        rng = np.random.default_rng(11)
        for cut in (300, 800):
            perturbed = series.copy()
            perturbed[cut:] = rng.normal(0.05, 0.30, series.size - cut)
            after = label_regimes(perturbed, ends(1_200), reference_id="REF")
            np.testing.assert_array_equal(
                np.asarray(after.trend)[:cut], np.asarray(base.trend)[:cut]
            )
            np.testing.assert_array_equal(
                np.asarray(after.volatility)[:cut], np.asarray(base.volatility)[:cut]
            )

    def test_a_whole_sample_quantile_would_fail_the_strong_form(self) -> None:
        """Pins that the test has teeth, by showing the forbidden estimator failing it."""
        series = reference_series(600, seed=13)
        cut = 200

        def offending(values: np.ndarray) -> np.ndarray:
            cuts = np.quantile(values, [1 / 3, 2 / 3])
            return np.searchsorted(cuts, values, side="right")

        perturbed = series.copy()
        perturbed[cut:] = np.random.default_rng(17).normal(0.05, 0.30, series.size - cut)
        assert not np.array_equal(offending(series)[:cut], offending(perturbed)[:cut])

    def test_the_expanding_classifier_never_uses_the_point_itself(self) -> None:
        """Changing observation ``k`` cannot change the label of any earlier one."""
        values = np.random.default_rng(19).normal(0.0, 1.0, 400)
        base = expanding_quantile_states(values, 3, warmup=60)
        moved = values.copy()
        moved[250] = 1_000.0
        after = expanding_quantile_states(moved, 3, warmup=60)
        np.testing.assert_array_equal(after[:250], base[:250])
        assert after[250] != base[250]

    def test_labels_misaligned_with_the_returns_are_refused(self) -> None:
        series = reference_series(400, seed=23)
        labels = label_regimes(series, ends(400), reference_id="REF")
        with pytest.raises(LookaheadError, match="exactly the same periods"):
            attribute_by_regime(periods(np.zeros(399)), labels)


class TestAnalyticCases:
    def test_quantile_buckets_split_evenly_in_the_long_run(self) -> None:
        values = np.random.default_rng(29).normal(0.0, 1.0, 4_000)
        states = expanding_quantile_states(values, 4, warmup=200)
        defined = states[states != UNDEFINED_STATE]
        shares = np.bincount(defined, minlength=4) / defined.size
        assert bool(np.all(np.abs(shares - 0.25) < 0.03))

    def test_the_warm_up_is_undefined_and_nothing_else_is(self) -> None:
        labels = label_regimes(reference_series(500, seed=31), ends(500), reference_id="REF")
        assert labels.warmup == DEFAULT_STATES_PER_AXIS * MIN_STATE_OBS
        trend = np.asarray(labels.trend)
        assert bool(np.all(trend[: labels.warmup] == UNDEFINED_STATE))
        assert bool(np.all(trend[labels.warmup :] != UNDEFINED_STATE))

    def test_the_joint_index_is_the_pair_and_nothing_more(self) -> None:
        labels = label_regimes(reference_series(400, seed=37), ends(400), reference_id="REF")
        joint = np.asarray(labels.joint())
        trend = np.asarray(labels.trend)
        volatility = np.asarray(labels.volatility)
        defined = joint != UNDEFINED_STATE
        expected = trend[defined] * labels.n_volatility_states + volatility[defined]
        np.testing.assert_array_equal(joint[defined], expected)

    def test_welch_reduces_to_the_classical_statistic_under_equal_variances(self) -> None:
        rng = np.random.default_rng(41)
        groups = [rng.normal(0.0, 1.0, 200) for _ in range(3)]
        assert welch_anova(groups).p_value == pytest.approx(f_oneway(*groups).pvalue, rel=0.25)

    def test_transition_rows_are_probabilities(self) -> None:
        labels = label_regimes(reference_series(1_200, seed=43), ends(1_200), reference_id="REF")
        transitions = transition_matrix(labels)
        matrix = np.asarray(transitions.matrix)
        np.testing.assert_allclose(matrix.sum(axis=1), 1.0, rtol=1e-12)
        assert bool(np.all(matrix >= 0.0))
        assert int(np.asarray(transitions.counts).sum()) == transitions.n_transitions

    def test_attribution_adds_up_to_the_total(self) -> None:
        series = reference_series(800, seed=47)
        labels = label_regimes(series, ends(800), reference_id="REF")
        values = np.random.default_rng(53).normal(0.0005, 0.01, 800)
        attribution = attribute_by_regime(periods(values), labels)
        assert sum(attribution.totals.values()) + attribution.undefined_total == pytest.approx(
            float(values.sum()), rel=1e-10
        )
        assert sum(attribution.counts.values()) + attribution.undefined_periods == 800


class TestInvariances:
    def test_a_monotone_transform_of_the_estimator_changes_no_label(self) -> None:
        """Quantile classification depends on order alone, so this must hold exactly."""
        values = np.random.default_rng(59).normal(0.0, 1.0, 600)
        base = expanding_quantile_states(values, 3, warmup=60)
        for transform in (lambda x: 7.0 * x, lambda x: x + 100.0, np.exp):
            np.testing.assert_array_equal(
                expanding_quantile_states(transform(values), 3, warmup=60), base
            )

    def test_labels_are_invariant_to_a_positive_rescaling_of_the_reference(self) -> None:
        series = reference_series(600, seed=61)
        base = label_regimes(series, ends(600), reference_id="REF")
        scaled = label_regimes(series * 4.0, ends(600), reference_id="REF")
        np.testing.assert_array_equal(np.asarray(scaled.trend), np.asarray(base.trend))
        np.testing.assert_array_equal(np.asarray(scaled.volatility), np.asarray(base.volatility))

    def test_markov_resampling_is_deterministic_under_a_seed(self) -> None:
        series = reference_series(800, seed=67)
        labels = label_regimes(series, ends(800), reference_id="REF")
        returns = periods(np.random.default_rng(71).normal(0.0004, 0.01, 800))
        first = markov_resample(returns, labels, n_paths=50, seed=3, allow_collapse=True)
        second = markov_resample(returns, labels, n_paths=50, seed=3, allow_collapse=True)
        np.testing.assert_array_equal(np.asarray(first.values), np.asarray(second.values))
        third = markov_resample(returns, labels, n_paths=50, seed=4, allow_collapse=True)
        assert not np.array_equal(np.asarray(first.values), np.asarray(third.values))

    def test_resampled_paths_only_contain_observed_returns(self) -> None:
        series = reference_series(600, seed=73)
        labels = label_regimes(series, ends(600), reference_id="REF")
        values = np.round(np.random.default_rng(79).normal(0.0, 0.01, 600), 12)
        paths = markov_resample(periods(values), labels, n_paths=30, seed=5, allow_collapse=True)
        steps = np.round(np.diff(np.asarray(paths.values), axis=1) / CAPITAL, 12)
        assert set(np.unique(steps).tolist()) <= set(np.unique(values).tolist())

    def test_paths_carry_the_period_unit_and_the_method(self) -> None:
        series = reference_series(600, seed=83)
        labels = label_regimes(series, ends(600), reference_id="REF")
        paths = markov_resample(
            periods(np.random.default_rng(89).normal(0.0, 0.01, 600)),
            labels,
            n_paths=10,
            seed=1,
            allow_collapse=True,
        )
        assert paths.period is Period.DAILY
        assert MARKOV_RESAMPLE in paths.method
        assert np.asarray(paths.values).shape == (10, 601)


class TestDegenerateCases:
    def test_a_sample_no_longer_than_the_warm_up_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="more than the warm up"):
            label_regimes(reference_series(60, seed=97), ends(60), reference_id="REF")

    def test_a_window_below_two_periods_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least two periods"):
            label_regimes(reference_series(400, seed=101), ends(400), reference_id="REF", window=1)

    def test_a_single_state_axis_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least two states"):
            expanding_quantile_states(np.zeros(100), 1, warmup=10)

    def test_a_warm_up_below_the_state_count_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least as long"):
            expanding_quantile_states(np.zeros(100), 5, warmup=3)

    def test_a_thin_state_is_refused_unless_the_collapse_is_authorised(self) -> None:
        """``02`` section 2.2, and the message names the remedy."""
        series = reference_series(400, seed=103)
        labels = label_regimes(series, ends(400), reference_id="REF")
        with pytest.raises(RegimeSparsityError, match="allow_collapse") as excinfo:
            transition_matrix(labels)
        assert excinfo.value.threshold == MIN_STATE_OBS
        collapsed = transition_matrix(labels, allow_collapse=True)
        assert collapsed.collapsed is True
        assert np.asarray(collapsed.matrix).shape[0] <= 4

    def test_welch_needs_two_usable_groups(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least two groups"):
            welch_anova([np.array([1.0, 2.0, 3.0]), np.array([1.0])])

    def test_welch_refuses_a_group_with_no_dispersion(self) -> None:
        with pytest.raises(InsufficientSampleError, match="unbounded weight"):
            welch_anova([np.full(30, 1.0), np.random.default_rng(107).normal(0.0, 1.0, 30)])

    def test_one_usable_state_leaves_the_test_absent_not_passed(self) -> None:
        """Absence of evidence is absence, never approval. See ``02`` section 7."""
        series = reference_series(400, seed=109)
        labels = label_regimes(series, ends(400), reference_id="REF")
        joint = np.asarray(labels.joint())
        values = np.zeros(400)
        values[joint == joint[joint != UNDEFINED_STATE][0]] = np.random.default_rng(113).normal(
            0.0, 0.01, int((joint == joint[joint != UNDEFINED_STATE][0]).sum())
        )
        attribution = attribute_by_regime(periods(values), labels)
        assert attribution.equality_of_means is None
        assert any("absence of a test is not evidence" in w for w in attribution.warnings)

    def test_thin_states_are_flagged_in_the_attribution(self) -> None:
        series = reference_series(400, seed=127)
        labels = label_regimes(series, ends(400), reference_id="REF")
        attribution = attribute_by_regime(
            periods(np.random.default_rng(131).normal(0.0, 0.01, 400)), labels
        )
        assert any("MIN_STATE_OBS" in w for w in attribution.warnings)

    def test_markov_resample_refuses_misaligned_labels(self) -> None:
        series = reference_series(400, seed=137)
        labels = label_regimes(series, ends(400), reference_id="REF")
        with pytest.raises(LookaheadError, match="same periods"):
            markov_resample(periods(np.zeros(399)), labels, n_paths=5, seed=1)

    def test_markov_resample_refuses_a_non_positive_shape(self) -> None:
        series = reference_series(600, seed=139)
        labels = label_regimes(series, ends(600), reference_id="REF")
        returns = periods(np.random.default_rng(149).normal(0.0, 0.01, 600))
        with pytest.raises(InsufficientSampleError, match="strictly positive"):
            markov_resample(returns, labels, n_paths=0, seed=1, allow_collapse=True)

    def test_current_equity_paths_compound(self) -> None:
        series = reference_series(600, seed=151)
        labels = label_regimes(series, ends(600), reference_id="REF")
        returns = periods(
            np.random.default_rng(157).normal(0.0, 0.01, 600), basis=Basis.CURRENT_EQUITY
        )
        paths = markov_resample(returns, labels, n_paths=20, seed=2, allow_collapse=True)
        assert bool(np.all(np.asarray(paths.values)[:, 0] == CAPITAL))

    def test_the_contract_refuses_a_malformed_grid(self) -> None:
        base = {
            "trend": np.zeros(10, dtype=np.int8),
            "volatility": np.zeros(10, dtype=np.int8),
            "period_end_ns": ends(10),
            "n_trend_states": 3,
            "n_volatility_states": 3,
            "window": 5,
            "warmup": 3,
            "reference_id": "REF",
        }
        with pytest.raises(SchemaError, match="at least 2"):
            RegimeLabels(**{**base, "n_trend_states": 1})  # type: ignore[arg-type]
        with pytest.raises(SchemaError, match="must lie in"):
            RegimeLabels(**{**base, "trend": np.full(10, 9, dtype=np.int8)})  # type: ignore[arg-type]
        with pytest.raises(SchemaError, match="window must be at least"):
            RegimeLabels(**{**base, "window": 1})  # type: ignore[arg-type]
        with pytest.raises(SchemaError, match="warmup must lie"):
            RegimeLabels(**{**base, "warmup": 99})  # type: ignore[arg-type]
        with pytest.raises(SchemaError, match="strictly increasing"):
            RegimeLabels(**{**base, "period_end_ns": np.zeros(10, dtype=np.int64)})  # type: ignore[arg-type]

    def test_empty_labels_are_refused(self) -> None:
        with pytest.raises(SchemaError, match="must not be empty"):
            RegimeLabels(
                trend=np.array([], dtype=np.int8),
                volatility=np.array([], dtype=np.int8),
                period_end_ns=np.array([], dtype=np.int64),
                n_trend_states=3,
                n_volatility_states=3,
                window=5,
                warmup=0,
                reference_id="REF",
            )

    def test_the_grid_size_is_the_product_of_the_axes(self) -> None:
        labels = label_regimes(
            reference_series(400, seed=191),
            ends(400),
            reference_id="REF",
            n_trend_states=2,
            n_volatility_states=3,
        )
        assert labels.n_states == 6

    def test_defaults_are_the_declared_ones(self) -> None:
        assert DEFAULT_STATES_PER_AXIS == 3
        assert DEFAULT_REGIME_WINDOW == 21


class TestSyntheticRecovery:
    def test_a_strategy_that_only_wins_in_high_volatility_is_identified(self) -> None:
        """``02`` section 4 acceptance, and the criterion of ``05`` v0.5."""
        series = reference_series(1_200, seed=5)
        labels = label_regimes(series, ends(1_200), reference_id="REF")
        volatility = np.asarray(labels.volatility)
        rng = np.random.default_rng(163)
        values = rng.normal(0.0, 0.004, 1_200)
        values[volatility == 2] += 0.010

        attribution = attribute_by_regime(periods(values), labels)
        top_axis = {
            state: total
            for state, total in attribution.totals.items()
            if state % labels.n_volatility_states == 2
        }
        share = sum(top_axis.values()) / sum(attribution.totals.values())
        assert share > 0.85
        assert attribution.equality_of_means is not None
        assert attribution.equality_of_means.p_value < 1e-6

    def test_a_strategy_indifferent_to_regime_is_not_flagged(self) -> None:
        """The other half of the pair: no false positive when no regime effect exists."""
        series = reference_series(1_200, seed=167)
        labels = label_regimes(series, ends(1_200), reference_id="REF")
        rejections = 0
        for seed in range(12):
            values = np.random.default_rng(200 + seed).normal(0.0005, 0.01, 1_200)
            attribution = attribute_by_regime(periods(values), labels)
            assert attribution.equality_of_means is not None
            rejections += int(attribution.equality_of_means.p_value < 0.05)
        assert rejections <= 2

    def test_a_known_transition_matrix_is_recovered(self) -> None:
        """``02`` section 2.2 acceptance, with the Monte Carlo error reported."""
        truth = np.array([[0.90, 0.10], [0.20, 0.80]])
        rng = np.random.default_rng(173)
        n_obs = 20_000
        chain = np.empty(n_obs, dtype=np.int8)
        chain[0] = 0
        for index in range(1, n_obs):
            chain[index] = int(rng.random() > truth[chain[index - 1], 0])
        labels = RegimeLabels(
            trend=np.ascontiguousarray(chain),
            volatility=np.zeros(n_obs, dtype=np.int8),
            period_end_ns=ends(n_obs),
            n_trend_states=2,
            n_volatility_states=2,
            window=5,
            warmup=0,
            reference_id="SYNTHETIC",
        )
        estimated = np.asarray(transition_matrix(labels).matrix)
        for row in range(2):
            occupancy = int((chain[:-1] == row).sum())
            for column in range(2):
                probability = truth[row, column]
                standard_error = np.sqrt(probability * (1.0 - probability) / occupancy)
                assert abs(estimated[row, column] - probability) < 4.0 * standard_error

    def test_markov_resampling_preserves_state_persistence(self) -> None:
        """What the scheme adds over a block bootstrap: runs of the same state."""
        truth = np.array([[0.95, 0.05], [0.10, 0.90]])
        rng = np.random.default_rng(179)
        n_obs = 3_000
        chain = np.empty(n_obs, dtype=np.int8)
        chain[0] = 0
        for index in range(1, n_obs):
            chain[index] = int(rng.random() > truth[chain[index - 1], 0])
        labels = RegimeLabels(
            trend=np.ascontiguousarray(chain),
            volatility=np.zeros(n_obs, dtype=np.int8),
            period_end_ns=ends(n_obs),
            n_trend_states=2,
            n_volatility_states=2,
            window=5,
            warmup=0,
            reference_id="SYNTHETIC",
        )
        values = np.where(chain == 0, 0.002, -0.002) + rng.normal(0.0, 0.001, n_obs)
        paths = markov_resample(periods(values), labels, n_paths=200, seed=9)
        steps = np.diff(np.asarray(paths.values), axis=1) / CAPITAL
        simulated = float(np.mean([np.corrcoef(p[1:], p[:-1])[0, 1] for p in steps]))
        observed = float(np.corrcoef(values[1:], values[:-1])[0, 1])
        assert simulated > 0.5 * observed
