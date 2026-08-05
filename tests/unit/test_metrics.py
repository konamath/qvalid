"""Tests for the descriptive statistics of section 1 of ``02``.

The four kinds required by ``04``:

``TestAnalyticCases``
    The reduction of the general delta method form to Mertens (2002), which is
    an exact identity and not an asymptotic statement; the single non zero
    period collapsing to ``1/sqrt(Y)``; the constant series being undefined
    rather than infinite.

``TestInvariances``
    Scale, sign, risk free translation, and the grid invariance of the Kelly
    fraction.

``TestDegenerateCases``
    Two periods, zero dispersion, no losing period, no winning period, ruin,
    forced bandwidth, tiny samples.

``TestSyntheticRecovery``
    AR(1) with a declared coefficient. The recovery criterion is stated as a
    bounded bias that shrinks with ``T``, not as "within sampling error",
    because the Bartlett HAC estimator has a finite sample bias that exceeds
    the Monte Carlo error by an order of magnitude at any usable ``T``. See
    D013.

``TestD006StructuralGuarantee``
    The prohibition on annualising trade indexed statistics, enforced by
    enumeration rather than by review.

``TestReproducibilityIsStructural``
    The ban on ``@`` inside ``core``, enforced by reading the syntax tree. The
    BLAS splits long reductions across threads, which changes the summation
    order, which breaks the byte equality of D030 on machines with different
    core counts. See D041.
"""

from __future__ import annotations

import ast
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from qvalid.contracts import Basis, Period, PeriodReturns, TradeReturns
from qvalid.core.constants import (
    DEFAULT_CONFIDENCE_LEVEL,
    MIN_PERIODS,
    WEEKDAYS_PER_YEAR,
    dilution_ratio_per_period,
)
from qvalid.core.metrics import (
    DrawdownProfile,
    PeriodMetrics,
    SharpeEstimate,
    TradeMetrics,
    bartlett_long_run_covariance,
    bartlett_long_run_variance,
    de_annualise_rate,
    drawdown_profile,
    equity_curve,
    inner_product,
    mertens_sharpe_variance,
    newey_west_bandwidth,
    newey_west_lag_selection,
    period_metrics,
    quadratic_form,
    sharpe_ratio,
    trade_metrics,
)
from qvalid.exceptions import InsufficientSampleError

CAPITAL = 100_000.0
DAY_NS = 86_400 * 1_000_000_000
EPOCH = int(datetime(2020, 1, 1, 21, 0, tzinfo=UTC).timestamp()) * 1_000_000_000


def make_periods(
    values: np.ndarray,
    *,
    period: Period = Period.DAILY,
    periods_per_year: float = WEEKDAYS_PER_YEAR,
    basis: Basis = Basis.FIXED_INITIAL,
    n_active: int | None = None,
) -> PeriodReturns:
    """Build a ``PeriodReturns`` directly, bypassing the grid projection.

    The projection is already tested in ``test_gridding.py``. Testing metrics
    through it would couple two failures into one signal.
    """
    values = np.ascontiguousarray(values, dtype=np.float64)
    ends = EPOCH + np.arange(values.size, dtype=np.int64) * DAY_NS
    return PeriodReturns(
        values=values,
        period_end_ns=ends,
        period=period,
        periods_per_year=periods_per_year,
        calendar_id="TEST",
        basis=basis,
        initial_capital=CAPITAL,
        n_active=int((values != 0.0).sum()) if n_active is None else n_active,
    )


def ar1(rho: float, n_obs: int, seed: int, *, sigma: float = 1.0, mean: float = 0.0) -> np.ndarray:
    """Stationary AR(1) path with a declared coefficient, burn in discarded."""
    rng = np.random.default_rng(seed)
    burn = 500
    shocks = rng.normal(0.0, sigma, n_obs + burn)
    out = np.empty(n_obs + burn, dtype=np.float64)
    out[0] = shocks[0] / math.sqrt(1.0 - rho * rho)
    for t in range(1, n_obs + burn):
        out[t] = rho * out[t - 1] + shocks[t]
    return out[burn:] + mean


class TestAnalyticCases:
    def test_general_delta_form_equals_mertens_exactly_at_bandwidth_zero(self) -> None:
        """``02`` section 1.3. Not a limit: the same number, for any finite sample."""
        rng = np.random.default_rng(2)
        for draw in (
            rng.normal(0.001, 0.01, 800),
            rng.gumbel(0.001, 0.008, 800),
            rng.standard_t(5, 800) * 0.006 + 0.001,
        ):
            series = make_periods(draw)
            estimate = sharpe_ratio(series, bandwidth=0)
            general = (estimate.standard_error / math.sqrt(series.periods_per_year)) ** 2
            assert general == pytest.approx(mertens_sharpe_variance(draw), rel=1e-12)

    def test_single_non_zero_period_gives_one_over_sqrt_years(self) -> None:
        """``02`` section 1.6, exact under the sample convention with denominator T-1."""
        for magnitude in (1e-6, 1.0, 1e4):
            values = np.zeros(500)
            values[-1] = magnitude
            series = make_periods(values)
            estimate = sharpe_ratio(series)
            assert estimate.per_period_sample == pytest.approx(1.0 / math.sqrt(500), rel=1e-10)
            assert estimate.annualised_sqrt_q == pytest.approx(
                1.0 / math.sqrt(series.years), rel=1e-10
            )

    def test_population_convention_gives_one_over_sqrt_t_minus_one(self) -> None:
        """The two conventions differ by exactly ``sqrt(T / (T-1))``, and both are reported."""
        values = np.zeros(500)
        values[-1] = 3.0
        estimate = sharpe_ratio(make_periods(values))
        assert estimate.per_period_population == pytest.approx(1.0 / math.sqrt(499), rel=1e-10)
        assert estimate.per_period_population / estimate.per_period_sample == pytest.approx(
            math.sqrt(500.0 / 499.0), rel=1e-12
        )

    def test_constant_series_is_undefined_not_infinite(self) -> None:
        """``02`` section 1.6, first bullet."""
        estimate = sharpe_ratio(make_periods(np.full(200, 0.001)))
        assert estimate.annualised_sqrt_q is None
        assert estimate.annualised_hac is None
        assert estimate.standard_error is None
        assert estimate.is_defined is False
        assert any("undefined rather" in w for w in estimate.warnings)

    def test_symmetric_series_has_null_skewness(self) -> None:
        """``02`` section 1.6, second bullet."""
        half = np.random.default_rng(4).normal(0.0, 0.01, 5_000)
        symmetric = np.concatenate([half, -half])
        metrics = trade_metrics(TradeReturns(symmetric, Basis.FIXED_INITIAL, CAPITAL))
        assert metrics.skewness == pytest.approx(0.0, abs=1e-12)

    def test_dilution_identity_holds_through_the_sharpe_function(self) -> None:
        """``02`` section 1.6 recovered on the population convention, which is exact."""
        rng = np.random.default_rng(6)
        values = np.zeros(1_000)
        active_idx = rng.choice(1_000, size=250, replace=False)
        values[active_idx] = rng.normal(0.004, 0.011, 250)
        grid = sharpe_ratio(make_periods(values))
        active = sharpe_ratio(make_periods(values[values != 0.0]))
        ratio = grid.per_period_population / active.per_period_population
        assert ratio == pytest.approx(
            dilution_ratio_per_period(0.25, active.per_period_population), rel=1e-12
        )

    def test_bartlett_weights_keep_the_long_run_variance_non_negative(self) -> None:
        rng = np.random.default_rng(8)
        for rho in (-0.8, -0.4, 0.0, 0.4, 0.8):
            series = ar1(rho, 400, 8)
            for bandwidth in (0, 1, 5, 20, 100):
                assert bartlett_long_run_variance(series, bandwidth) >= 0.0
            assert rng.random() >= 0.0

    def test_long_run_covariance_is_symmetric(self) -> None:
        rng = np.random.default_rng(9)
        matrix = rng.normal(0.0, 1.0, (300, 2))
        omega = bartlett_long_run_covariance(matrix, 7)
        np.testing.assert_allclose(omega, omega.T, rtol=1e-14)

    def test_lag_selection_matches_the_written_derivation(self) -> None:
        """``02`` section 1.4 anchors ``MIN_PERIODS`` on this quantity."""
        assert newey_west_lag_selection(MIN_PERIODS) == 3
        assert newey_west_lag_selection(100) == 4
        assert newey_west_lag_selection(1_000) == 6

    def test_geometric_risk_free_conversion_compounds_back(self) -> None:
        per_period = de_annualise_rate(0.045, WEEKDAYS_PER_YEAR)
        assert (1.0 + per_period) ** WEEKDAYS_PER_YEAR == pytest.approx(1.045, rel=1e-12)
        assert per_period < 0.045 / WEEKDAYS_PER_YEAR

    def test_drawdown_on_a_known_path(self) -> None:
        """Deterministic path: peak 120 at index 2, trough 60 at index 4, recovery at 7."""
        equity = np.array([100.0, 110.0, 120.0, 90.0, 60.0, 80.0, 110.0, 125.0])
        profile = drawdown_profile(equity)
        assert profile.max_drawdown == pytest.approx(0.5)
        assert profile.max_drawdown_duration == 5
        assert profile.recovered is True
        assert profile.time_underwater == pytest.approx(4.0 / 7.0)

    def test_unrecovered_drawdown_runs_to_the_end_of_the_sample(self) -> None:
        equity = np.array([100.0, 120.0, 90.0, 95.0, 99.0])
        profile = drawdown_profile(equity)
        assert profile.recovered is False
        assert profile.max_drawdown == pytest.approx(0.25)
        assert profile.max_drawdown_duration == 3


class TestInvariances:
    def test_sharpe_is_scale_invariant(self) -> None:
        """``02`` section 1.6, third bullet."""
        base = np.random.default_rng(12).normal(0.001, 0.01, 400)
        first = sharpe_ratio(make_periods(base))
        second = sharpe_ratio(make_periods(base * 13.7))
        assert second.annualised_sqrt_q == pytest.approx(first.annualised_sqrt_q, rel=1e-11)
        assert second.annualised_hac == pytest.approx(first.annualised_hac, rel=1e-11)
        assert second.bandwidth == first.bandwidth

    def test_sign_flip_flips_the_sharpe_and_keeps_the_interval_width(self) -> None:
        base = np.random.default_rng(14).normal(0.001, 0.01, 400)
        first = sharpe_ratio(make_periods(base))
        second = sharpe_ratio(make_periods(-base))
        assert second.annualised_sqrt_q == pytest.approx(-first.annualised_sqrt_q, rel=1e-11)
        assert second.ci_high - second.ci_low == pytest.approx(
            first.ci_high - first.ci_low, rel=1e-9
        )

    def test_risk_free_rate_translates_the_numerator_only(self) -> None:
        base = np.random.default_rng(16).normal(0.001, 0.01, 400)
        zero = sharpe_ratio(make_periods(base), risk_free_rate=0.0)
        paid = sharpe_ratio(make_periods(base), risk_free_rate=0.05)
        assert paid.annualised_sqrt_q < zero.annualised_sqrt_q
        assert paid.sample_variance == pytest.approx(zero.sample_variance, rel=1e-12)
        assert paid.risk_free_rate_annual == 0.05
        assert paid.risk_free_rate_per_period == de_annualise_rate(0.05, WEEKDAYS_PER_YEAR)

    def test_kelly_fraction_is_invariant_to_the_grid_step(self) -> None:
        """Mean and variance both scale with the period length, so the ratio does not.

        Aggregating a daily series into non overlapping blocks of five is the
        exact analogue of coarsening the grid, and the Kelly fraction has to
        survive it. If it did not, the grid projection would be distorting the
        moments rather than merely rescaling them.
        """
        daily = np.random.default_rng(18).normal(0.0008, 0.009, 5_000)
        weekly = daily.reshape(-1, 5).sum(axis=1)
        fine = period_metrics(make_periods(daily)).kelly_fraction
        coarse = period_metrics(
            make_periods(weekly, period=Period.WEEKLY, periods_per_year=WEEKDAYS_PER_YEAR / 5.0)
        ).kelly_fraction
        assert coarse == pytest.approx(fine, rel=0.06)

    def test_forced_bandwidth_of_zero_recovers_the_sample_variance_scaling(self) -> None:
        base = np.random.default_rng(20).normal(0.001, 0.01, 400)
        estimate = sharpe_ratio(make_periods(base), bandwidth=0)
        assert estimate.long_run_variance == pytest.approx(base.var(ddof=0), rel=1e-12)
        assert estimate.bandwidth == 0


class TestDegenerateCases:
    def test_fewer_than_two_periods_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least two periods"):
            sharpe_ratio(make_periods(np.array([0.01])))

    def test_below_min_periods_warns_but_still_reports(self) -> None:
        """``02`` section 1.4 says report with a warning, not raise."""
        estimate = sharpe_ratio(make_periods(np.random.default_rng(22).normal(0.001, 0.01, 30)))
        assert estimate.is_defined
        assert any("MIN_PERIODS" in w for w in estimate.warnings)

    def test_below_min_trades_warns_but_still_reports(self) -> None:
        values = np.random.default_rng(24).normal(0.001, 0.01, 10)
        metrics = trade_metrics(TradeReturns(values, Basis.FIXED_INITIAL, CAPITAL))
        assert metrics.n_trades == 10
        assert any("MIN_TRADES" in w for w in metrics.warnings)

    def test_empty_trade_series_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least one trade"):
            trade_metrics(TradeReturns(np.array([], dtype=np.float64), Basis.FIXED_INITIAL, 1.0))

    def test_all_winning_trades_leave_profit_factor_undefined(self) -> None:
        metrics = trade_metrics(TradeReturns(np.full(50, 0.002), Basis.FIXED_INITIAL, CAPITAL))
        assert metrics.profit_factor is None
        assert metrics.win_loss_ratio is None
        assert metrics.mean_loss is None
        assert metrics.hit_rate == 1.0

    def test_all_losing_trades_give_a_profit_factor_of_zero(self) -> None:
        metrics = trade_metrics(TradeReturns(np.full(50, -0.002), Basis.FIXED_INITIAL, CAPITAL))
        assert metrics.profit_factor == 0.0
        assert metrics.hit_rate == 0.0
        assert metrics.mean_win is None

    def test_scratch_trades_are_neither_win_nor_loss(self) -> None:
        values = np.array([0.01, 0.0, 0.0, -0.01])
        metrics = trade_metrics(TradeReturns(values, Basis.FIXED_INITIAL, CAPITAL))
        assert (metrics.n_wins, metrics.n_losses) == (1, 1)
        assert metrics.hit_rate == 0.25
        assert metrics.n_trades - metrics.n_wins - metrics.n_losses == 2

    def test_no_losing_period_leaves_sortino_undefined(self) -> None:
        metrics = period_metrics(make_periods(np.full(200, 0.001)))
        assert metrics.sortino_annualised is None
        assert any("Sortino" in w for w in metrics.warnings)

    def test_ruin_makes_cagr_and_drawdown_undefined_not_wrong(self) -> None:
        values = np.full(120, -0.01)
        values[0] = -1.5
        metrics = period_metrics(make_periods(values))
        assert metrics.cumulative_return < -1.0
        assert metrics.cagr is None
        assert metrics.drawdown is None
        assert any("compound growth rate is undefined" in w for w in metrics.warnings)
        assert any("drawdown not computed" in w for w in metrics.warnings)

    def test_current_equity_basis_builds_a_multiplicative_path(self) -> None:
        values = np.array([0.10, -0.10, 0.10])
        path = equity_curve(make_periods(values, basis=Basis.CURRENT_EQUITY))
        assert path[-1] == pytest.approx(CAPITAL * 1.10 * 0.90 * 1.10, rel=1e-12)
        additive = equity_curve(make_periods(values, basis=Basis.FIXED_INITIAL))
        assert additive[-1] == pytest.approx(CAPITAL * 1.10, rel=1e-12)

    def test_drawdown_needs_two_points(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least two equity"):
            drawdown_profile(np.array([100.0]))

    def test_bandwidth_outside_range_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match=r"\[0, T-1\]"):
            bartlett_long_run_variance(np.arange(10, dtype=np.float64), 10)

    def test_lag_selection_needs_an_observation(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least one observation"):
            newey_west_lag_selection(0)

    def test_bandwidth_of_a_constant_series_is_zero(self) -> None:
        assert newey_west_bandwidth(np.full(200, 3.0)) == 0

    def test_mertens_refuses_zero_dispersion(self) -> None:
        with pytest.raises(InsufficientSampleError, match="zero dispersion"):
            mertens_sharpe_variance(np.full(50, 0.01))

    def test_rate_below_minus_one_is_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="must exceed -1"):
            de_annualise_rate(-1.5, 252.0)

    def test_two_observation_guards_on_the_hac_helpers(self) -> None:
        single = np.array([1.0])
        with pytest.raises(InsufficientSampleError):
            newey_west_bandwidth(single)
        with pytest.raises(InsufficientSampleError):
            bartlett_long_run_variance(single, 0)
        with pytest.raises(InsufficientSampleError):
            bartlett_long_run_covariance(single.reshape(1, 1), 0)
        with pytest.raises(InsufficientSampleError):
            mertens_sharpe_variance(single)

    def test_sparse_grid_carries_the_dilution_warning(self) -> None:
        values = np.zeros(200)
        values[::4] = 0.01
        metrics = period_metrics(make_periods(values))
        assert any("active_fraction" in w for w in metrics.warnings)


class TestSyntheticRecovery:
    @pytest.mark.parametrize("rho", [-0.3, 0.3, 0.5])
    def test_hac_scaling_moves_in_the_direction_the_sign_of_rho_predicts(self, rho: float) -> None:
        """``02`` section 1.6, penultimate bullet, direction half."""
        series = make_periods(ar1(rho, 3_000, 41, sigma=0.01, mean=0.0005))
        estimate = sharpe_ratio(series)
        if rho > 0.0:
            assert estimate.annualised_hac < estimate.annualised_sqrt_q
        else:
            assert estimate.annualised_hac > estimate.annualised_sqrt_q

    def test_eta_recovery_bias_is_bounded_and_shrinks_with_sample_size(self) -> None:
        """``02`` section 1.6, penultimate bullet, magnitude half.

        The criterion is not "within sampling error". The Bartlett HAC
        estimator is biased downward in finite samples, so the recovered ratio
        ``sigma / sigma_LR`` is biased toward one, and at ``T = 2000`` that bias
        is seven Monte Carlo standard errors wide. Asserting it away with a
        loose tolerance would be the forbidden move of ``04``. The honest
        criterion is that the bias is bounded and consistent. See D013.
        """
        rho = 0.4
        theory = math.sqrt((1.0 - rho) / (1.0 + rho))
        biases = []
        for n_obs in (500, 2_000, 8_000):
            ratios = []
            for seed in range(40):
                series = ar1(rho, n_obs, 900 + seed)
                bandwidth = newey_west_bandwidth(series)
                long_run = bartlett_long_run_variance(series, bandwidth)
                ratios.append(float(series.std(ddof=0)) / math.sqrt(long_run))
            biases.append(float(np.mean(ratios)) / theory - 1.0)
        assert all(0.0 < b < 0.10 for b in biases), biases
        assert biases[0] > biases[1] > biases[2], biases

    def test_iid_series_recovers_a_small_bandwidth(self) -> None:
        bandwidths = [newey_west_bandwidth(ar1(0.0, 2_000, 300 + s)) for s in range(20)]
        assert float(np.median(bandwidths)) < 25.0

    def test_declared_sharpe_is_recovered_within_its_own_interval(self) -> None:
        """Coverage check: the interval contains the truth at close to the nominal rate."""
        periods_per_year = WEEKDAYS_PER_YEAR
        mu, sigma = 0.0006, 0.010
        truth = math.sqrt(periods_per_year) * mu / sigma
        covered = 0
        trials = 200
        for seed in range(trials):
            rng = np.random.default_rng(5_000 + seed)
            series = make_periods(rng.normal(mu, sigma, 1_000))
            estimate = sharpe_ratio(series)
            covered += int(estimate.ci_low <= truth <= estimate.ci_high)
        rate = covered / trials
        standard_error = math.sqrt(0.95 * 0.05 / trials)
        assert abs(rate - 0.95) < 4.0 * standard_error, rate


class TestD006StructuralGuarantee:
    FORBIDDEN = ("periods_per_year", "period", "calendar_id", "years")

    @pytest.mark.parametrize("field_name", FORBIDDEN)
    def test_trade_metrics_carries_no_calendar_field(self, field_name: str) -> None:
        assert not hasattr(TradeMetrics, "__dataclass_fields__") or (
            field_name not in TradeMetrics.__dataclass_fields__
        ), f"D006 foi revertida: TradeMetrics passou a expor {field_name}"

    def test_trade_metrics_has_no_annualised_field(self) -> None:
        offending = [
            name
            for name in TradeMetrics.__dataclass_fields__
            if "annual" in name.lower() or "cagr" in name.lower()
        ]
        assert not offending, f"D006 foi revertida: campos anualizados em TradeMetrics {offending}"

    def test_annualising_functions_reject_trade_returns(self) -> None:
        series = TradeReturns(
            np.random.default_rng(26).normal(0.001, 0.01, 200), Basis.FIXED_INITIAL, CAPITAL
        )
        with pytest.raises(AttributeError):
            sharpe_ratio(series)  # type: ignore[arg-type]
        with pytest.raises(AttributeError):
            period_metrics(series)  # type: ignore[arg-type]

    def test_period_metrics_declares_every_field_that_changes_the_number(self) -> None:
        """``01`` requires the report to be reproducible from these."""
        required = {
            "period",
            "periods_per_year",
            "calendar_id",
            "basis",
            "initial_capital",
            "active_fraction",
        }
        assert required <= set(PeriodMetrics.__dataclass_fields__)
        sharpe_required = {
            "risk_free_rate_annual",
            "risk_free_rate_per_period",
            "bandwidth",
            "confidence_level",
        }
        assert sharpe_required <= set(SharpeEstimate.__dataclass_fields__)

    def test_contracts_are_frozen(self) -> None:
        estimate = sharpe_ratio(make_periods(np.random.default_rng(28).normal(0.001, 0.01, 200)))
        for obj in (estimate, period_metrics(make_periods(np.full(50, 0.001) + 1e-4))):
            with pytest.raises((AttributeError, TypeError)):
                obj.n_periods = 1  # type: ignore[misc]
        assert isinstance(estimate, SharpeEstimate)

    def test_default_confidence_level_is_reported(self) -> None:
        estimate = sharpe_ratio(make_periods(np.random.default_rng(30).normal(0.001, 0.01, 200)))
        assert estimate.confidence_level == DEFAULT_CONFIDENCE_LEVEL
        assert isinstance(
            period_metrics(make_periods(np.full(80, 0.001) + 1e-5)).drawdown, DrawdownProfile
        )


class TestReproducibilityIsStructural:
    """D041. The BLAS is banned from ``core`` by enumeration, not by review."""

    def test_no_module_in_core_contains_a_matrix_product(self) -> None:
        """``@`` dispatches to the BLAS, whose reduction order depends on threads.

        Reviewing every diff for a stray ``@`` is exactly the discipline this
        project does not rely on anywhere else. Reading the syntax tree costs
        nothing and cannot be forgotten.
        """
        core = Path(__file__).resolve().parents[2] / "src" / "qvalid" / "core"
        offenders = [
            f"{path.name}:{node.lineno}"
            for path in sorted(core.glob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.MatMult)
        ]
        assert offenders == [], (
            f"matrix product inside core at {offenders}; use inner_product or "
            "quadratic_form, whose summation order does not depend on the BLAS "
            "thread count. See D041."
        )

    @pytest.mark.parametrize("size", [2, 17, 1_000, 50_000])
    def test_the_replacement_agrees_with_the_operator_it_replaces(self, size: int) -> None:
        """Below the threading threshold the two are bit identical, so pin that."""
        rng = np.random.default_rng(41)
        left = rng.normal(size=size)
        right = rng.normal(size=size)
        assert inner_product(left, right) == pytest.approx(float(left @ right), rel=1e-12)

    def test_the_quadratic_form_agrees_with_the_operator_it_replaces(self) -> None:
        rng = np.random.default_rng(43)
        matrix = rng.normal(size=(3, 3))
        vector = rng.normal(size=3)
        assert quadratic_form(vector, matrix) == pytest.approx(
            float(vector @ matrix @ vector), rel=1e-12
        )

    def test_a_strided_column_gives_the_same_answer_as_its_contiguous_copy(self) -> None:
        """``_cross_moment_matrix`` feeds it column slices, which are not contiguous.

        Pairwise summation takes a different code path for a strided input, so
        the property that the answer depends only on the values, and not on how
        they sit in memory, has to be pinned rather than assumed.
        """
        rng = np.random.default_rng(47)
        matrix = rng.normal(size=(5_000, 3))
        for i in range(3):
            for j in range(3):
                strided = inner_product(matrix[:, i], matrix[:, j])
                contiguous = inner_product(
                    np.ascontiguousarray(matrix[:, i]), np.ascontiguousarray(matrix[:, j])
                )
                assert strided == contiguous

    def test_the_long_run_covariance_is_still_symmetric_after_the_change(self) -> None:
        """The property the assembled matrix could plausibly have lost."""
        rng = np.random.default_rng(53)
        matrix = np.column_stack([rng.normal(size=800), rng.normal(size=800) ** 2])
        omega = bartlett_long_run_covariance(matrix, 6)
        np.testing.assert_allclose(omega, omega.T, rtol=0.0, atol=0.0)
