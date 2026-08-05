"""Tests for the stationary bootstrap and the block length estimator.

The four kinds required by ``04``:

``TestAnalyticCases``
    The realised block length matches the requested one; ``b = 1`` degenerates
    to i.i.d. resampling exactly; the flat top window has the shape it claims.

``TestInvariances``
    Determinism under a fixed seed, independence across seeds, scale
    invariance of the paths, and the fact that resampling permutes the
    multiset of observed values rather than inventing new ones.

``TestDegenerateCases``
    Constant series, two observations, one path, one step, block length above
    the sample, and the ratio guard of ``02`` section 2.1.

``TestSyntheticRecovery``
    The estimate near 1 under i.i.d., matching the brute force mean squared
    error minimiser under AR(1), scaling as ``n^(1/3)``, preserving first order
    autocorrelation strictly better than i.i.d. resampling, and covering the
    true mean at the nominal rate.

The brute force check is the one that matters. Transcribing a formula from a
paper and asserting the transcription proves nothing; agreeing with the block
length that actually minimises the mean squared error of the estimator being
optimised is evidence.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from qvalid.contracts import Basis, EquityPaths, Period, PeriodReturns, TradeReturns, Unit
from qvalid.core.constants import MIN_BLOCK_SAMPLE_RATIO
from qvalid.core.metrics import equity_curve
from qvalid.core.resample import (
    STATIONARY_BOOTSTRAP,
    BlockLengthEstimate,
    estimate_block_length,
    resample_equity_paths,
    stationary_bootstrap,
    stationary_bootstrap_indices,
)
from qvalid.exceptions import InsufficientSampleError

CAPITAL = 100_000.0
DAY_NS = 86_400 * 1_000_000_000


def ar1(rho: float, n_obs: int, seed: int, *, sigma: float = 1.0) -> np.ndarray:
    """Stationary AR(1) path with a declared coefficient, burn in discarded."""
    rng = np.random.default_rng(seed)
    burn = 500
    shocks = rng.normal(0.0, sigma, n_obs + burn)
    out = np.empty(n_obs + burn, dtype=np.float64)
    out[0] = shocks[0] / math.sqrt(1.0 - rho * rho)
    for t in range(1, n_obs + burn):
        out[t] = rho * out[t - 1] + shocks[t]
    return np.ascontiguousarray(out[burn:])


def make_periods(values: np.ndarray, *, basis: Basis = Basis.FIXED_INITIAL) -> PeriodReturns:
    values = np.ascontiguousarray(values, dtype=np.float64)
    return PeriodReturns(
        values=values,
        period_end_ns=np.arange(values.size, dtype=np.int64) * DAY_NS,
        period=Period.DAILY,
        periods_per_year=260.89,
        calendar_id="TEST",
        basis=basis,
        initial_capital=CAPITAL,
        n_active=int((values != 0.0).sum()),
    )


def first_order_autocorrelation(matrix: np.ndarray) -> np.ndarray:
    centred = matrix - matrix.mean(axis=1, keepdims=True)
    return (centred[:, 1:] * centred[:, :-1]).sum(axis=1) / (centred * centred).sum(axis=1)


def exact_stationary_bootstrap_variance(values: np.ndarray, block_length: float) -> float:
    """Politis and Romano (1994) variance of the bootstrap sample mean.

    This is the quantity the Politis and White block length is chosen to
    estimate well. It is written out here rather than imported, because a test
    that reuses the implementation it is checking proves only self consistency.
    """
    n_obs = values.size
    centred = values - values.mean()
    lags = np.arange(n_obs)
    autocov = np.array([float(centred[k:] @ centred[: n_obs - k]) / n_obs for k in lags])
    carry = 1.0 - 1.0 / block_length
    weights = ((n_obs - lags) / n_obs) * carry**lags + (lags / n_obs) * carry ** (n_obs - lags)
    return float(autocov[0] * weights[0] + 2.0 * (autocov[1:] * weights[1:]).sum())


class TestAnalyticCases:
    @pytest.mark.parametrize("block_length", [1.0, 2.0, 5.0, 20.0])
    def test_realised_block_length_matches_the_requested_one(self, block_length: float) -> None:
        """A block continues when the next index is the previous one plus one, modulo n."""
        n_obs = 500
        index = stationary_bootstrap_indices(
            n_obs, n_paths=300, n_steps=n_obs, block_length=block_length, seed=7
        )
        continues = (np.diff(index, axis=1) % n_obs) == 1
        realised = 1.0 / (1.0 - continues.mean())
        assert realised == pytest.approx(block_length, rel=0.05)

    def test_block_length_one_is_exactly_iid_resampling(self) -> None:
        """Every step starts a new block, so the indices are independent and uniform."""
        n_obs = 50
        index = stationary_bootstrap_indices(
            n_obs, n_paths=4_000, n_steps=20, block_length=1.0, seed=11
        )
        counts = np.bincount(index.ravel(), minlength=n_obs)
        expected = index.size / n_obs
        chi_square = float(((counts - expected) ** 2 / expected).sum())
        assert chi_square < 2.0 * n_obs
        continues = (np.diff(index, axis=1) % n_obs) == 1
        assert continues.mean() == pytest.approx(1.0 / n_obs, abs=0.01)

    def test_indices_wrap_circularly(self) -> None:
        """Wrapping is part of the method, not a convenience: every index stays in range."""
        index = stationary_bootstrap_indices(
            17, n_paths=200, n_steps=200, block_length=50.0, seed=13
        )
        assert index.min() >= 0
        assert index.max() <= 16
        assert set(np.unique(index).tolist()) == set(range(17))

    def test_equity_path_construction_matches_the_observed_curve_rule(self) -> None:
        """One rule for observed and simulated, so the two are comparable."""
        values = ar1(0.0, 200, 3) / 500.0
        for basis in (Basis.FIXED_INITIAL, Basis.CURRENT_EQUITY):
            series = make_periods(values, basis=basis)
            result = resample_equity_paths(series, n_paths=1, seed=5, block_length=1.0)
            resampled, _ = stationary_bootstrap(values, n_paths=1, seed=5, block_length=1.0)
            reference = equity_curve(make_periods(resampled[0], basis=basis))
            np.testing.assert_allclose(np.asarray(result.paths.values)[0], reference, rtol=1e-12)

    def test_paths_start_at_the_initial_capital(self) -> None:
        result = resample_equity_paths(make_periods(ar1(0.0, 100, 17) / 400.0), n_paths=50, seed=2)
        values = np.asarray(result.paths.values)
        assert values.shape == (50, 101)
        assert bool(np.all(values[:, 0] == CAPITAL))


class TestInvariances:
    def test_same_seed_gives_identical_paths(self) -> None:
        series = make_periods(ar1(0.3, 300, 19) / 400.0)
        first = resample_equity_paths(series, n_paths=100, seed=42)
        second = resample_equity_paths(series, n_paths=100, seed=42)
        np.testing.assert_array_equal(
            np.asarray(first.paths.values), np.asarray(second.paths.values)
        )

    def test_different_seeds_give_different_paths(self) -> None:
        series = make_periods(ar1(0.3, 300, 19) / 400.0)
        first = resample_equity_paths(series, n_paths=100, seed=42)
        second = resample_equity_paths(series, n_paths=100, seed=43)
        assert not np.array_equal(np.asarray(first.paths.values), np.asarray(second.paths.values))

    def test_no_global_random_state_is_touched(self) -> None:
        """``04``: no module level randomness, so a legacy seed must not matter."""
        series = make_periods(ar1(0.2, 200, 23) / 400.0)
        np.random.seed(1)  # noqa: NPY002
        first = resample_equity_paths(series, n_paths=20, seed=9)
        np.random.seed(2)  # noqa: NPY002
        second = resample_equity_paths(series, n_paths=20, seed=9)
        np.testing.assert_array_equal(
            np.asarray(first.paths.values), np.asarray(second.paths.values)
        )

    def test_resampling_only_permutes_the_observed_values(self) -> None:
        """The bootstrap cannot invent a return the sample never showed."""
        values = np.round(ar1(0.4, 150, 29) / 300.0, 12)
        resampled, _ = stationary_bootstrap(values, n_paths=200, seed=31)
        assert set(np.unique(resampled).tolist()) <= set(np.unique(values).tolist())

    def test_scaling_returns_scales_the_pnl_of_every_path(self) -> None:
        """Under ``FIXED_INITIAL`` the P&L is linear in the returns, so it scales exactly.

        The equity *level* does not scale, because the capital is an additive
        offset. Asserting that it does would be asserting a false invariance,
        which is how this test was first written and why it failed.
        """
        values = ar1(0.3, 250, 37) / 400.0
        base = resample_equity_paths(make_periods(values), n_paths=30, seed=3, block_length=6.0)
        scaled = resample_equity_paths(
            make_periods(values * 3.0), n_paths=30, seed=3, block_length=6.0
        )
        base_pnl = np.asarray(base.paths.values) - CAPITAL
        scaled_pnl = np.asarray(scaled.paths.values) - CAPITAL
        # Tolerance derived, not chosen. A cumulative sum of n terms accumulates
        # at most n * eps * max|partial sum| of rounding, and the partial sums
        # cross zero here, so a relative tolerance is the wrong instrument.
        bound = (
            base_pnl.shape[1] * float(np.finfo(np.float64).eps) * float(np.abs(scaled_pnl).max())
        )
        np.testing.assert_allclose(scaled_pnl, 3.0 * base_pnl, rtol=0.0, atol=bound)
        assert bound < 1e-7

    def test_block_length_estimate_is_scale_invariant(self) -> None:
        values = ar1(0.5, 600, 41)
        assert estimate_block_length(values).block_length == pytest.approx(
            estimate_block_length(values * 1_000.0).block_length, rel=1e-9
        )

    def test_unit_propagates_from_the_input_and_is_never_chosen(self) -> None:
        trade = TradeReturns(
            np.ascontiguousarray(ar1(0.0, 200, 43) / 500.0), Basis.FIXED_INITIAL, CAPITAL
        )
        trade_result = resample_equity_paths(trade, n_paths=10, seed=1)
        assert trade_result.paths.unit is Unit.TRADE
        assert trade_result.paths.period is None

        period_result = resample_equity_paths(
            make_periods(ar1(0.0, 200, 43) / 500.0), n_paths=10, seed=1
        )
        assert period_result.paths.unit is Unit.PERIOD
        assert period_result.paths.period is Period.DAILY

    def test_provenance_is_carried_on_the_result(self) -> None:
        series = make_periods(ar1(0.4, 400, 47) / 400.0)
        result = resample_equity_paths(series, n_paths=10, seed=8)
        assert result.paths.method == STATIONARY_BOOTSTRAP
        assert result.paths.seed == 8
        assert result.basis is Basis.FIXED_INITIAL
        assert result.initial_capital == CAPITAL
        assert result.block_length.automatic is True
        assert result.block_length.n_obs == 400
        assert result.unit is Unit.PERIOD

    def test_supplied_block_length_is_marked_non_automatic(self) -> None:
        series = make_periods(ar1(0.4, 400, 53) / 400.0)
        result = resample_equity_paths(series, n_paths=10, seed=8, block_length=12.0)
        assert result.block_length.automatic is False
        assert result.block_length.block_length == 12.0
        assert any("overriding automatic" in w for w in result.warnings)


class TestDegenerateCases:
    def test_constant_series_falls_back_to_iid(self) -> None:
        estimate = estimate_block_length(np.full(300, 0.001))
        assert estimate.block_length == 1.0
        assert any("no dispersion" in w for w in estimate.warnings)

    def test_minimum_sample_gives_a_typed_error_not_an_index_error(self) -> None:
        """Two observations. This case crashed before the bandwidth was capped at ``n - 1``.

        Any sample below ``MIN_BLOCK_SAMPLE_RATIO`` observations is refused by
        the ratio guard, since the smallest possible block length is 1 and the
        limit is ``n / 10``. The bandwidth cap is what makes the refusal arrive
        as the typed error the caller can act on instead of an ``IndexError``
        from indexing lag 7 of a two element autocovariance vector.
        """
        with pytest.raises(InsufficientSampleError, match="independent blocks"):
            estimate_block_length(np.array([1.0, -1.0]))

    def test_the_ratio_guard_refuses_every_sample_below_the_ratio(self) -> None:
        for n_obs in (2, 5, 9):
            with pytest.raises(InsufficientSampleError, match="independent blocks"):
                estimate_block_length(ar1(0.0, n_obs, 79))

    @pytest.mark.parametrize("n_obs", [2, 3, 5, 8, 13, 21])
    def test_short_samples_never_raise_an_untyped_error(self, n_obs: int) -> None:
        """Every sample size down to the minimum returns a typed answer or a typed error."""
        try:
            estimate = estimate_block_length(ar1(0.4, n_obs, 73))
        except InsufficientSampleError:
            return
        assert 1.0 <= estimate.block_length <= estimate.cap

    def test_one_observation_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least two observations"):
            estimate_block_length(np.array([1.0]))

    def test_single_path_and_single_step(self) -> None:
        index = stationary_bootstrap_indices(10, n_paths=1, n_steps=1, block_length=3.0, seed=1)
        assert index.shape == (1, 1)
        assert 0 <= int(index[0, 0]) <= 9

    def test_non_positive_shape_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="strictly positive"):
            stationary_bootstrap_indices(10, n_paths=0, n_steps=5, block_length=2.0, seed=1)

    def test_block_length_below_one_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least 1"):
            stationary_bootstrap_indices(10, n_paths=2, n_steps=5, block_length=0.5, seed=1)

    def test_block_length_above_the_sample_still_works(self) -> None:
        """A block longer than the series is legal: it wraps and repeats."""
        index = stationary_bootstrap_indices(
            10, n_paths=5, n_steps=40, block_length=1_000.0, seed=1
        )
        assert index.shape == (5, 40)
        assert bool(np.all((np.diff(index, axis=1) % 10) == 1))

    def test_ratio_guard_aborts_on_a_persistent_short_sample(self) -> None:
        """``02`` section 2.1: fewer than ten independent blocks voids the coverage claim."""
        with pytest.raises(InsufficientSampleError, match="independent blocks") as excinfo:
            estimate_block_length(ar1(0.95, 120, 59))
        assert excinfo.value.threshold == pytest.approx(120 / MIN_BLOCK_SAMPLE_RATIO)

    def test_capped_estimate_is_flagged(self) -> None:
        estimate = estimate_block_length(ar1(0.9, 4_000, 61))
        assert estimate.block_length <= estimate.cap
        assert estimate.block_length > 1.0

    def test_longer_horizon_than_the_sample_is_allowed(self) -> None:
        """Risk of ruin over a declared horizon needs paths longer than the history."""
        series = make_periods(ar1(0.2, 200, 67) / 400.0)
        result = resample_equity_paths(series, n_paths=10, seed=1, n_steps=1_000)
        assert np.asarray(result.paths.values).shape == (10, 1_001)

    def test_ruin_under_current_equity_is_reported_not_raised(self) -> None:
        """A resampled path can concatenate losses the history never showed in a row."""
        values = np.full(60, -0.05)
        values[0] = 0.05
        values[1] = -1.0
        series = make_periods(values, basis=Basis.CURRENT_EQUITY)
        result = resample_equity_paths(series, n_paths=200, seed=1, block_length=1.0)
        assert isinstance(result.paths, EquityPaths)
        assert float(np.asarray(result.paths.values).min()) <= 0.0
        assert any("that is ruin, not an error" in w for w in result.warnings)

    def test_empty_source_series_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least one observation"):
            stationary_bootstrap_indices(0, n_paths=2, n_steps=5, block_length=1.0, seed=1)

    def test_estimate_is_frozen(self) -> None:
        estimate = estimate_block_length(ar1(0.3, 300, 71))
        assert isinstance(estimate, BlockLengthEstimate)
        with pytest.raises((AttributeError, TypeError)):
            estimate.block_length = 2.0  # type: ignore[misc]


class TestSyntheticRecovery:
    def test_iid_series_gives_a_block_length_near_one(self) -> None:
        """``02`` section 2.1 acceptance, first half."""
        lengths = [estimate_block_length(ar1(0.0, 2_000, 100 + s)).block_length for s in range(30)]
        assert float(np.mean(lengths)) < 2.0

    def test_estimate_is_monotone_in_the_ar_coefficient(self) -> None:
        means = []
        for rho in (0.0, 0.2, 0.4, 0.6, 0.8):
            lengths = [
                estimate_block_length(ar1(rho, 2_000, 200 + s)).block_length for s in range(12)
            ]
            means.append(float(np.mean(lengths)))
        assert means == sorted(means)
        assert means[0] < 2.5
        assert means[-1] > 20.0

    def test_estimate_scales_as_the_cube_root_of_the_sample(self) -> None:
        """Ratios across doublings must sit near ``2 ** (1/3)``, which is 1.26."""
        means = []
        for n_obs in (500, 1_000, 2_000, 4_000):
            lengths = [
                estimate_block_length(ar1(0.5, n_obs, 300 + s)).block_length for s in range(12)
            ]
            means.append(float(np.mean(lengths)))
        ratios = [b / a for a, b in zip(means[:-1], means[1:], strict=True)]
        assert all(1.15 < r < 1.45 for r in ratios), ratios

    def test_estimate_matches_the_brute_force_mse_minimiser(self) -> None:
        """The verification that matters, and it does not depend on the paper.

        Under AR(1) with coefficient 0.5 the true long run variance is
        ``1 / (1 - rho) ** 2 = 4``. Sweeping the exact stationary bootstrap
        variance estimator over a grid of block lengths and taking the argmin of
        the mean squared error gives the block length the plug in rule is trying
        to find. Agreement between the two is evidence; agreement between the
        implementation and a transcription of the formula is not.
        """
        rho, n_obs = 0.5, 1_000
        truth = 1.0 / (1.0 - rho) ** 2
        paths = [ar1(rho, n_obs, 400 + s) for s in range(40)]
        grid = np.arange(4, 25, 2, dtype=float)
        errors = [
            float(
                np.mean([(exact_stationary_bootstrap_variance(x, b) - truth) ** 2 for x in paths])
            )
            for b in grid
        ]
        brute_force = float(grid[int(np.argmin(errors))])
        plug_in = float(np.mean([estimate_block_length(x).block_length for x in paths]))
        assert abs(plug_in - brute_force) <= 3.0, (plug_in, brute_force)

    def test_block_resampling_preserves_autocorrelation_better_than_iid(self) -> None:
        """``02`` section 2.1 acceptance, second half, with the margin measured."""
        rho = 0.6
        values = ar1(rho, 1_000, 500)
        observed = float(np.corrcoef(values[1:], values[:-1])[0, 1])
        block, estimate = stationary_bootstrap(values, n_paths=400, seed=1)
        iid, _ = stationary_bootstrap(values, n_paths=400, seed=1, block_length=1.0)
        block_rho = float(first_order_autocorrelation(block).mean())
        iid_rho = float(first_order_autocorrelation(iid).mean())
        assert abs(iid_rho) < 0.05
        assert block_rho > 0.8 * observed
        assert abs(block_rho - observed) < abs(iid_rho - observed)
        assert estimate.block_length > 5.0

    def test_bootstrap_distribution_of_the_mean_covers_at_the_nominal_rate(self) -> None:
        """Coverage under i.i.d. data, where the percentile interval is well calibrated."""
        true_mean, sigma, n_obs = 0.0008, 0.01, 400
        covered = 0
        trials = 150
        for seed in range(trials):
            rng = np.random.default_rng(6_000 + seed)
            sample = rng.normal(true_mean, sigma, n_obs)
            paths, _ = stationary_bootstrap(sample, n_paths=400, seed=seed)
            means = paths.mean(axis=1)
            low, high = np.percentile(means, [2.5, 97.5])
            covered += int(low <= true_mean <= high)
        rate = covered / trials
        standard_error = math.sqrt(0.95 * 0.05 / trials)
        assert abs(rate - 0.95) < 4.0 * standard_error, rate

    def test_monte_carlo_standard_error_shrinks_with_replications(self) -> None:
        """``02`` general rules: a Monte Carlo estimate comes with its own error."""
        values = ar1(0.3, 500, 700) / 400.0
        errors = []
        for n_paths in (100, 400, 1_600):
            paths, _ = stationary_bootstrap(values, n_paths=n_paths, seed=5)
            means = paths.mean(axis=1)
            errors.append(float(means.std(ddof=1) / math.sqrt(n_paths)))
        ratios = [a / b for a, b in zip(errors[:-1], errors[1:], strict=True)]
        assert all(1.6 < r < 2.4 for r in ratios), ratios
