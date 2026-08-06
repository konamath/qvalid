"""Tests for the search corrections of section 3 of ``02``.

``TestTheDiscriminationPair`` is the reason this version exists. ``05`` calls it
the most important test in the project: a synthetic strategy built to be pure
noise must be refused and a synthetic strategy with a declared edge must be
accepted, by every instrument in the module. Everything else here supports it.

The remaining classes cover the four kinds required by ``04``: exact identities
against ``core/metrics.py``, invariances including the ordering of the three SPA
p values, degenerate cases, and recovery of declared parameters.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from qvalid.contracts import Basis, Period, SchemaError, TrialMatrix
from qvalid.core.constants import EULER_MASCHERONI
from qvalid.core.metrics import mertens_sharpe_variance
from qvalid.core.overfit import (
    DEFAULT_CSCV_SPLITS,
    OverfitInputError,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    superior_predictive_ability,
)
from qvalid.exceptions import InsufficientSampleError

DAY_NS = 86_400 * 1_000_000_000
SIGMA = 0.01


def matrix(values: np.ndarray) -> TrialMatrix:
    n_periods, n_configs = values.shape
    return TrialMatrix(
        values=np.ascontiguousarray(values, dtype=np.float64),
        config_ids=np.array([f"c{j:03d}" for j in range(n_configs)]),
        period_end_ns=np.arange(n_periods, dtype=np.int64) * DAY_NS,
        period=Period.DAILY,
        periods_per_year=260.89,
        calendar_id="TEST",
        basis=Basis.FIXED_INITIAL,
        initial_capital=100_000.0,
    )


def noise_matrix(n_periods: int, n_configs: int, seed: int) -> np.ndarray:
    """Every configuration is pure noise. No edge exists to be found."""
    return np.random.default_rng(seed).normal(0.0, SIGMA, (n_periods, n_configs))


def edge_matrix(n_periods: int, n_configs: int, seed: int, mu: float = 0.0012) -> np.ndarray:
    """Configuration zero carries a declared edge; the rest are noise."""
    values = np.random.default_rng(seed).normal(0.0, SIGMA, (n_periods, n_configs))
    values[:, 0] += mu
    return values


def best_column(values: np.ndarray) -> np.ndarray:
    sharpe = values.mean(axis=0) / values.std(axis=0, ddof=0)
    return np.ascontiguousarray(values[:, int(np.argmax(sharpe))])


def trial_variance(values: np.ndarray) -> float:
    return float((values.mean(axis=0) / values.std(axis=0, ddof=0)).var(ddof=1))


class TestTheDiscriminationPair:
    """The criterion of ``05`` v0.4, measured over seeds rather than on one draw.

    Averaging matters here. With 12870 splits the probability of backtest
    overfitting still ranges from 0.26 to 0.58 across seeds under pure noise, so
    an assertion pinned to a single draw would be pinning that draw's luck. The
    thresholds below come from the measurement recorded in the module docstring
    and in D025.
    """

    SEEDS = (10, 11, 12, 13)
    STRONG_EDGE = 0.0025
    """Per period Sharpe of 0.25 against a dispersion of 0.01."""

    def _instruments(self, values: np.ndarray) -> tuple[float, float, float, float]:
        trials = matrix(values)
        pbo = probability_of_backtest_overfitting(trials)
        deflated = deflated_sharpe_ratio(
            best_column(values), n_trials=values.shape[1], trial_variance=trial_variance(values)
        )
        spa = superior_predictive_ability(
            trials, np.zeros(values.shape[0]), seed=1, n_bootstrap=300
        )
        return (
            pbo.probability,
            pbo.median_logit,
            deflated.probability,
            spa.p_value_consistent,
        )

    def test_pure_noise_is_refused_by_every_instrument(self) -> None:
        results = [self._instruments(noise_matrix(1_000, 50, seed=s)) for s in self.SEEDS]
        pbo, logit, deflated, spa = (np.array(x) for x in zip(*results, strict=True))
        assert 0.40 < pbo.mean() < 0.60
        assert abs(logit.mean()) < 0.6
        assert deflated.mean() < 0.70
        # Not ``all(spa > 0.05)``. The studentised SPA over rejects at this
        # sample size, nine per cent against a nominal five, which is measured
        # and recorded in the module docstring. Demanding zero false positives
        # over four seeds would contradict that measurement, so the assertion is
        # on the typical p value instead.
        assert float(np.median(spa)) > 0.05
        assert float(spa.mean()) > 0.15

    def test_a_declared_edge_is_accepted_by_every_instrument(self) -> None:
        results = [
            self._instruments(edge_matrix(1_000, 50, seed=s, mu=self.STRONG_EDGE))
            for s in self.SEEDS
        ]
        pbo, logit, deflated, spa = (np.array(x) for x in zip(*results, strict=True))
        assert bool(np.all(pbo < 0.02))
        # The logit is bounded by log(N): with fifty configurations the rank of
        # the winner cannot exceed 50/51, so 3.912 is the ceiling and not a
        # coincidence. Its magnitude is therefore not comparable across
        # universes of different size.
        ceiling = math.log(50.0)
        assert bool(np.all(logit > 0.9 * ceiling))
        assert bool(np.all(logit <= ceiling + 1e-9))
        assert bool(np.all(deflated > 0.99))
        assert bool(np.all(spa < 0.01))

    def test_the_uncorrected_number_is_the_one_that_lies(self) -> None:
        """The whole argument of the module, in one assertion.

        The winner of fifty noise configurations has a probabilistic Sharpe
        above 0.95 against a threshold of zero. Correcting for the fact that it
        was selected out of fifty drops it to a coin flip. A report that shows
        only the first number is the defect this project exists to fix.
        """
        for seed in self.SEEDS:
            values = noise_matrix(1_000, 50, seed=seed)
            deflated = deflated_sharpe_ratio(
                best_column(values), n_trials=50, trial_variance=trial_variance(values)
            )
            assert deflated.psr_against_zero > 0.90
            assert deflated.probability < deflated.psr_against_zero - 0.25

    def test_a_moderate_edge_is_where_the_three_instruments_disagree(self) -> None:
        """An honest limit of the module, asserted instead of left to be discovered.

        At a per period Sharpe of 0.12 against forty nine noise competitors, the
        cross validation and the superiority test both find the edge, while the
        deflated Sharpe ranges from 0.54 to 0.97 across seeds and is therefore
        the least decisive of the three. That is not a defect: the deflation
        answers a harder question, whether the *level* of the Sharpe survives
        the expected maximum of fifty trials, and at this effect size it barely
        does. The panel of ``02`` section 7 exists so that this disagreement is
        shown rather than averaged away.
        """
        deflated_values, pbo_values = [], []
        for seed in self.SEEDS:
            values = edge_matrix(1_000, 50, seed=seed, mu=0.0012)
            pbo_values.append(probability_of_backtest_overfitting(matrix(values)).probability)
            deflated_values.append(
                deflated_sharpe_ratio(
                    best_column(values), n_trials=50, trial_variance=trial_variance(values)
                ).probability
            )
        assert float(np.mean(pbo_values)) < 0.35
        assert float(np.min(deflated_values)) < 0.90
        assert float(np.max(deflated_values)) > 0.90


class TestAnalyticCases:
    def test_psr_denominator_equals_the_mertens_variance_exactly(self) -> None:
        """An identity, not an approximation, and it ties this module to metrics."""
        for seed, draw in enumerate(
            (
                np.random.default_rng(1).normal(0.0008, SIGMA, 400),
                np.random.default_rng(2).standard_t(5, 400) * SIGMA + 0.0008,
                np.random.default_rng(3).gumbel(0.0008, SIGMA, 400),
            )
        ):
            psr = probabilistic_sharpe_ratio(draw)
            assert psr.denominator == pytest.approx(
                math.sqrt(draw.size * mertens_sharpe_variance(draw)), rel=1e-12
            ), seed

    def test_expected_maximum_grows_with_the_number_of_trials(self) -> None:
        values = [expected_maximum_sharpe(n, 0.04) for n in (2, 5, 10, 50, 200, 1_000)]
        assert values == sorted(values)
        assert values[0] > 0.0

    def test_expected_maximum_scales_with_the_root_of_the_variance(self) -> None:
        assert expected_maximum_sharpe(100, 0.16) == pytest.approx(
            2.0 * expected_maximum_sharpe(100, 0.04), rel=1e-12
        )

    def test_expected_maximum_matches_simulation_within_the_declared_error(self) -> None:
        """The approximation is asymptotic in ``N``, and the docstring says by how much."""
        rng = np.random.default_rng(11)
        for n_trials, tolerance in ((10, 0.05), (200, 0.02), (1_000, 0.02)):
            simulated = rng.normal(0.0, 0.2, (20_000, n_trials)).max(axis=1).mean()
            closed = expected_maximum_sharpe(n_trials, 0.04)
            assert closed == pytest.approx(simulated, rel=tolerance)

    def test_euler_mascheroni_is_the_declared_constant(self) -> None:
        assert pytest.approx(EULER_MASCHERONI, abs=1e-15) == 0.5772156649015329

    def test_psr_is_one_half_when_the_sharpe_equals_the_benchmark(self) -> None:
        values = np.random.default_rng(13).normal(0.0008, SIGMA, 500)
        observed = probabilistic_sharpe_ratio(values).observed_sharpe
        at_the_threshold = probabilistic_sharpe_ratio(values, benchmark_sharpe=observed)
        assert at_the_threshold.probability == pytest.approx(0.5, abs=1e-12)

    def test_minimum_track_record_length_matches_the_closed_form(self) -> None:
        values = np.random.default_rng(17).normal(0.0012, SIGMA, 600)
        returns = matrix(values.reshape(-1, 1)).column("c000")
        psr = probabilistic_sharpe_ratio(values)
        result = minimum_track_record_length(returns, target_probability=0.95)
        expected = 1.0 + psr.denominator**2 * (float(norm.ppf(0.95)) / psr.observed_sharpe) ** 2
        assert result.periods == pytest.approx(expected, rel=1e-6)
        assert result.years == pytest.approx(result.periods / returns.periods_per_year, rel=1e-12)

    def test_pbo_is_symmetric_in_the_number_of_combinations(self) -> None:
        trials = matrix(noise_matrix(400, 8, seed=19))
        result = probability_of_backtest_overfitting(trials, n_splits=10)
        assert result.n_combinations == math.comb(10, 5)
        assert result.periods_used == 400


class TestInvariances:
    def test_psr_is_scale_invariant(self) -> None:
        values = np.random.default_rng(23).normal(0.0008, SIGMA, 400)
        assert probabilistic_sharpe_ratio(values).probability == pytest.approx(
            probabilistic_sharpe_ratio(values * 17.0).probability, rel=1e-10
        )

    def test_pbo_is_scale_invariant(self) -> None:
        values = noise_matrix(400, 10, seed=29)
        first = probability_of_backtest_overfitting(matrix(values), n_splits=10)
        second = probability_of_backtest_overfitting(matrix(values * 5.0), n_splits=10)
        assert first.probability == second.probability

    def test_pbo_is_invariant_to_relabelling_the_configurations(self) -> None:
        values = noise_matrix(400, 10, seed=31)
        permuted = values[:, ::-1]
        assert probability_of_backtest_overfitting(
            matrix(values), n_splits=10
        ).probability == pytest.approx(
            probability_of_backtest_overfitting(matrix(permuted), n_splits=10).probability
        )

    @pytest.mark.parametrize("seed", [41, 42, 43])
    def test_the_three_spa_p_values_are_always_ordered(self, seed: int) -> None:
        """``p_lower <= p_consistent <= p_upper`` by construction of the recentring."""
        values = np.random.default_rng(seed).normal(0.0, SIGMA, (400, 15))
        values[:, 1:] -= 0.004
        spa = superior_predictive_ability(matrix(values), np.zeros(400), seed=seed, n_bootstrap=300)
        assert spa.p_value_lower <= spa.p_value_consistent + 1e-12
        assert spa.p_value_consistent <= spa.p_value_upper + 1e-12

    def test_spa_is_deterministic_under_a_fixed_seed(self) -> None:
        values = noise_matrix(400, 10, seed=47)
        first = superior_predictive_ability(matrix(values), np.zeros(400), seed=5, n_bootstrap=200)
        second = superior_predictive_ability(matrix(values), np.zeros(400), seed=5, n_bootstrap=200)
        assert first.p_value_consistent == second.p_value_consistent
        third = superior_predictive_ability(matrix(values), np.zeros(400), seed=6, n_bootstrap=200)
        assert third.seed == 6

    def test_a_benchmark_equal_to_the_best_configuration_cannot_be_beaten(self) -> None:
        values = edge_matrix(400, 6, seed=53)
        spa = superior_predictive_ability(
            matrix(values), np.ascontiguousarray(values[:, 0]), seed=1, n_bootstrap=300
        )
        assert spa.p_value_consistent > 0.10

    def test_deflation_is_monotone_in_the_number_of_trials(self) -> None:
        """Searching harder can only lower the deflated probability."""
        values = edge_matrix(800, 20, seed=59)
        winner = best_column(values)
        variance = trial_variance(values)
        probabilities = [
            deflated_sharpe_ratio(winner, n_trials=n, trial_variance=variance).probability
            for n in (2, 10, 100, 10_000)
        ]
        assert probabilities == sorted(probabilities, reverse=True)


class TestDegenerateCases:
    def test_a_single_trial_is_not_a_search(self) -> None:
        values = np.random.default_rng(61).normal(0.0008, SIGMA, 300)
        with pytest.raises(OverfitInputError, match="no search to correct for"):
            deflated_sharpe_ratio(values, n_trials=1, trial_variance=0.01)

    def test_identical_trials_carry_no_information_about_the_search(self) -> None:
        values = np.random.default_rng(67).normal(0.0008, SIGMA, 300)
        with pytest.raises(OverfitInputError, match="positive variance"):
            deflated_sharpe_ratio(values, n_trials=50, trial_variance=0.0)

    def test_the_input_error_is_distinguishable_from_a_sample_size_error(self) -> None:
        """D004: the remedy is to declare the search, not to collect more data."""
        assert issubclass(OverfitInputError, InsufficientSampleError)

    def test_zero_dispersion_has_no_sharpe_and_therefore_no_correction(self) -> None:
        with pytest.raises(InsufficientSampleError, match="zero dispersion"):
            probabilistic_sharpe_ratio(np.full(300, 0.001))

    def test_three_observations_are_the_minimum_for_the_shape_moments(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least three observations"):
            probabilistic_sharpe_ratio(np.array([0.01, -0.01]))

    def test_a_sharpe_below_the_benchmark_needs_an_infinite_track_record(self) -> None:
        """Answered, not raised. See D064.

        This used to expect ``InsufficientSampleError``, whose name told the
        reader to collect more data while the message it carried told them that
        more data would not help. An infinite requirement is the most
        informative thing this function ever returns and it is now returned.
        """
        values = np.random.default_rng(71).normal(-0.0005, SIGMA, 400)
        returns = matrix(values.reshape(-1, 1)).column("c000")
        result = minimum_track_record_length(returns)
        assert result.attainable is False
        assert result.periods is None and result.years is None
        assert result.sufficient is False
        assert result.observed_sharpe <= result.benchmark_sharpe

    def test_an_attainable_length_carries_the_number_and_the_flag(self) -> None:
        values = np.random.default_rng(72).normal(0.0015, SIGMA, 400)
        returns = matrix(values.reshape(-1, 1)).column("c000")
        result = minimum_track_record_length(returns)
        assert result.attainable is True
        assert result.periods is not None and result.periods > 0.0
        assert result.observed_sharpe > result.benchmark_sharpe

    def test_target_probability_outside_the_unit_interval_is_refused(self) -> None:
        values = np.random.default_rng(73).normal(0.0015, SIGMA, 400)
        returns = matrix(values.reshape(-1, 1)).column("c000")
        with pytest.raises(InsufficientSampleError, match="open interval"):
            minimum_track_record_length(returns, target_probability=1.0)

    def test_odd_split_counts_are_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="even"):
            probability_of_backtest_overfitting(matrix(noise_matrix(200, 5, 79)), n_splits=7)

    def test_one_configuration_is_not_a_selection(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least two configurations"):
            probability_of_backtest_overfitting(matrix(noise_matrix(200, 1, 83)), n_splits=10)

    def test_blocks_too_small_for_a_sharpe_are_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="two observations"):
            probability_of_backtest_overfitting(matrix(noise_matrix(20, 5, 89)), n_splits=16)

    def test_a_benchmark_of_the_wrong_length_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="exactly the periods"):
            superior_predictive_ability(
                matrix(noise_matrix(200, 5, 97)), np.zeros(199), seed=1, n_bootstrap=100
            )

    def test_the_dropped_tail_of_the_sample_is_reported(self) -> None:
        """407 periods into 10 blocks uses 400 and says so rather than absorbing it."""
        result = probability_of_backtest_overfitting(matrix(noise_matrix(407, 6, 101)), n_splits=10)
        assert result.periods_used == 400

    def test_constant_columns_do_not_crash_the_cross_validation(self) -> None:
        values = noise_matrix(300, 6, seed=103)
        values[:, 2] = 0.0
        result = probability_of_backtest_overfitting(matrix(values), n_splits=10)
        assert 0.0 <= result.probability <= 1.0

    def test_default_splits_are_the_declared_ones(self) -> None:
        assert DEFAULT_CSCV_SPLITS == 16


class TestTrialMatrixContract:
    def test_the_grid_is_declared_once_for_every_configuration(self) -> None:
        """The precondition of ``02`` section 3 made structural rather than checked."""
        trials = matrix(noise_matrix(100, 4, seed=107))
        columns = [trials.column(name) for name in trials.config_ids]
        assert len({c.periods_per_year for c in columns}) == 1
        assert len({c.period for c in columns}) == 1
        assert len({c.calendar_id for c in columns}) == 1

    def test_an_unknown_configuration_is_refused(self) -> None:
        trials = matrix(noise_matrix(100, 4, seed=109))
        with pytest.raises(SchemaError, match="not in the matrix"):
            trials.column("c999")

    def test_duplicate_identifiers_are_refused(self) -> None:
        with pytest.raises(SchemaError, match="unique"):
            TrialMatrix(
                values=np.zeros((10, 2)) + 0.001,
                config_ids=np.array(["a", "a"]),
                period_end_ns=np.arange(10, dtype=np.int64) * DAY_NS,
                period=Period.DAILY,
                periods_per_year=260.89,
                calendar_id="T",
                basis=Basis.FIXED_INITIAL,
                initial_capital=1.0,
            )

    def test_years_uses_the_declared_rate(self) -> None:
        trials = matrix(noise_matrix(261, 3, seed=127))
        assert trials.years == pytest.approx(261 / 260.89, rel=1e-12)

    def test_a_supplied_block_length_overrides_the_estimate(self) -> None:
        values = noise_matrix(300, 5, seed=131)
        result = superior_predictive_ability(
            matrix(values), np.zeros(300), seed=1, n_bootstrap=200, block_length=7.0
        )
        assert result.block_length == 7.0

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("config_ids", np.array([1, 2, 3]), "string array"),
            ("periods_per_year", 0.0, "periods_per_year must be positive"),
            ("initial_capital", -1.0, "initial_capital must be positive"),
        ],
    )
    def test_malformed_fields_are_refused(self, field: str, value: object, match: str) -> None:
        kwargs: dict[str, object] = {
            "values": np.zeros((10, 3)) + 0.001,
            "config_ids": np.array(["a", "b", "c"]),
            "period_end_ns": np.arange(10, dtype=np.int64) * DAY_NS,
            "period": Period.DAILY,
            "periods_per_year": 260.89,
            "calendar_id": "T",
            "basis": Basis.FIXED_INITIAL,
            "initial_capital": 1.0,
        }
        kwargs[field] = value
        with pytest.raises(SchemaError, match=match):
            TrialMatrix(**kwargs)  # type: ignore[arg-type]

    def test_shape_and_alignment_are_refused_when_wrong(self) -> None:
        base: dict[str, object] = {
            "period_end_ns": np.arange(10, dtype=np.int64) * DAY_NS,
            "period": Period.DAILY,
            "periods_per_year": 260.89,
            "calendar_id": "T",
            "basis": Basis.FIXED_INITIAL,
            "initial_capital": 1.0,
        }
        with pytest.raises(SchemaError, match="at least two periods"):
            TrialMatrix(values=np.zeros((1, 2)), config_ids=np.array(["a", "b"]), **base)  # type: ignore[arg-type]
        with pytest.raises(SchemaError, match="one entry per column"):
            TrialMatrix(values=np.zeros((10, 3)), config_ids=np.array(["a", "b"]), **base)  # type: ignore[arg-type]
        with pytest.raises(SchemaError, match="one entry per period"):
            TrialMatrix(
                values=np.zeros((9, 2)),
                config_ids=np.array(["a", "b"]),
                **{**base, "period_end_ns": np.arange(10, dtype=np.int64) * DAY_NS},  # type: ignore[arg-type]
            )
        with pytest.raises(SchemaError, match="strictly increasing"):
            TrialMatrix(
                values=np.zeros((10, 2)),
                config_ids=np.array(["a", "b"]),
                **{**base, "period_end_ns": np.zeros(10, dtype=np.int64)},  # type: ignore[arg-type]
            )

    def test_the_matrix_is_read_only(self) -> None:
        trials = matrix(noise_matrix(50, 3, seed=113))
        with pytest.raises(ValueError, match="read-only"):
            trials.values[0, 0] = 1.0


class TestSyntheticRecovery:
    def test_spa_recovers_the_power_the_reality_check_loses(self) -> None:
        """Hansen (2005), the reason the SPA is the default and the RC the comparison.

        With poor models in the universe the reality check must treat them as
        possibly at the boundary, which inflates its critical value. The
        consistent recentring drops them. Measured over 200 replications at
        ``n = 500`` the powers are 0.960 against 0.745; this test uses fewer
        replications and asserts the ordering with a margin derived from the
        binomial error of the estimate.
        """
        trials_count = 60
        consistent_rejections = 0
        reality_check_rejections = 0
        for seed in range(trials_count):
            rng = np.random.default_rng(5_000 + seed)
            values = rng.normal(0.0, SIGMA, (500, 20))
            values[:, 0] += 0.0015
            values[:, 1:] -= 0.004
            spa = superior_predictive_ability(
                matrix(values), np.zeros(500), seed=seed, n_bootstrap=300
            )
            consistent_rejections += int(spa.p_value_consistent < 0.05)
            reality_check_rejections += int(spa.p_value_reality_check < 0.05)
        assert consistent_rejections > reality_check_rejections
        margin = 2.0 * math.sqrt(0.25 / trials_count) * trials_count
        assert consistent_rejections - reality_check_rejections > margin

    def test_the_two_tests_agree_when_the_universe_holds_no_poor_models(self) -> None:
        """Nothing to drop means nothing to gain, which is the other half of the claim."""
        agreements = 0
        for seed in range(30):
            rng = np.random.default_rng(6_000 + seed)
            values = rng.normal(0.0, SIGMA, (400, 15))
            values[:, 0] += 0.0015
            spa = superior_predictive_ability(
                matrix(values), np.zeros(400), seed=seed, n_bootstrap=300
            )
            agreements += int((spa.p_value_consistent < 0.05) == (spa.p_value_reality_check < 0.05))
        assert agreements >= 27

    def test_pbo_recovers_one_half_under_noise_across_seeds(self) -> None:
        """Half is the correct answer under noise, not a failure of the method."""
        probabilities = [
            probability_of_backtest_overfitting(
                matrix(noise_matrix(600, 20, seed=200 + s)), n_splits=10
            ).probability
            for s in range(8)
        ]
        assert 0.40 < float(np.mean(probabilities)) < 0.60

    def test_pbo_falls_as_the_edge_grows(self) -> None:
        probabilities = [
            probability_of_backtest_overfitting(
                matrix(edge_matrix(600, 20, seed=300, mu=mu)), n_splits=10
            ).probability
            for mu in (0.0, 0.0020, 0.0035)
        ]
        assert probabilities == sorted(probabilities, reverse=True)
        assert probabilities[0] - probabilities[-1] > 0.30

    def test_deflated_sharpe_recovers_the_declared_threshold(self) -> None:
        """The threshold a strategy must clear is the expected maximum of the trials."""
        values = edge_matrix(800, 40, seed=307)
        winner = best_column(values)
        variance = trial_variance(values)
        result = deflated_sharpe_ratio(winner, n_trials=40, trial_variance=variance)
        assert result.expected_maximum == pytest.approx(
            expected_maximum_sharpe(40, variance), rel=1e-12
        )
        assert result.psr.benchmark_sharpe == result.expected_maximum
