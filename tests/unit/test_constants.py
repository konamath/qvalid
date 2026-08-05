"""Tests for core.constants.

The point of this module is that the derivations written in the docstrings are
executed, not merely asserted in prose. If someone edits MIN_ACTIVE_FRACTION
without redoing the kurtosis algebra, these tests fail.
"""

from __future__ import annotations

import math

import pytest

from qvalid.contracts import Period
from qvalid.core import constants as const


class TestDerivationCoverage:
    """Every public constant must carry a written derivation."""

    def test_every_public_constant_has_a_derivation(self) -> None:
        exported = {name for name in const.__all__ if name.isupper() and name != "DERIVATIONS"}
        missing = exported - set(const.DERIVATIONS)
        assert not missing, f"constants without a derivation: {sorted(missing)}"

    def test_no_orphan_derivations(self) -> None:
        orphans = set(const.DERIVATIONS) - set(const.__all__)
        assert not orphans, f"derivations for non existent constants: {sorted(orphans)}"

    def test_derivations_are_substantive(self) -> None:
        too_short = {k for k, v in const.DERIVATIONS.items() if len(v) < 40}
        assert not too_short, f"derivations too short to be justifications: {sorted(too_short)}"


class TestSparsityKurtosis:
    """Analytic case. The identity is exact, so the tolerance is machine epsilon."""

    def test_minimum_at_one_half(self) -> None:
        assert const.sparsity_kurtosis(0.5) == pytest.approx(1.0, abs=1e-15)

    def test_root_gives_kurtosis_six(self) -> None:
        root = const.SPARSITY_KURTOSIS_ROOT
        assert const.sparsity_kurtosis(root) == pytest.approx(6.0, abs=1e-12)

    def test_root_matches_closed_form(self) -> None:
        assert pytest.approx(0.127322, abs=1e-6) == const.SPARSITY_KURTOSIS_ROOT

    def test_threshold_keeps_induced_excess_below_two(self) -> None:
        excess = const.sparsity_kurtosis(const.MIN_ACTIVE_FRACTION) - 3.0
        assert excess < 2.0
        assert excess == pytest.approx(1.843, abs=1e-3)

    def test_threshold_sits_above_the_root(self) -> None:
        assert const.MIN_ACTIVE_FRACTION > const.SPARSITY_KURTOSIS_ROOT

    def test_symmetric_about_one_half(self) -> None:
        for p in (0.05, 0.15, 0.3, 0.49):
            assert const.sparsity_kurtosis(p) == pytest.approx(const.sparsity_kurtosis(1.0 - p))

    def test_monotone_decreasing_below_one_half(self) -> None:
        grid = [0.02, 0.05, 0.1, 0.15, 0.25, 0.4, 0.5]
        values = [const.sparsity_kurtosis(p) for p in grid]
        assert all(a > b for a, b in zip(values, values[1:], strict=False))

    def test_diverges_as_series_becomes_degenerate(self) -> None:
        assert const.sparsity_kurtosis(1e-6) > 1e5

    @pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.5])
    def test_rejects_p_outside_open_unit_interval(self, p: float) -> None:
        with pytest.raises(ValueError):
            const.sparsity_kurtosis(p)


class TestSparsityKurtosisAgainstSimulation:
    """Property under synthetic data. The closed form must match the sample moment."""

    @pytest.mark.parametrize("p", [0.1, 0.15, 0.3])
    @pytest.mark.parametrize("magnitude", [1.0, 1e4])
    def test_recovers_sample_kurtosis(self, p: float, magnitude: float) -> None:
        import numpy as np

        rng = np.random.default_rng(20260804)
        n = 2_000_000
        series = np.where(rng.random(n) < p, magnitude, 0.0)
        centred = series - series.mean()
        m2 = float((centred**2).mean())
        m4 = float((centred**4).mean())
        empirical = m4 / (m2 * m2)
        assert empirical == pytest.approx(const.sparsity_kurtosis(p), rel=0.05)


class TestDegenerateAnnualSharpe:
    """Analytic case of 02 section 1.6, verified against the direct computation."""

    @pytest.mark.parametrize("years", [1.0, 3.0, 5.0, 10.0])
    def test_closed_form(self, years: float) -> None:
        assert const.degenerate_annual_sharpe(years) == pytest.approx(1.0 / math.sqrt(years))

    def test_five_years_gives_the_documented_value(self) -> None:
        assert const.degenerate_annual_sharpe(5.0) == pytest.approx(0.4472, abs=1e-4)

    @pytest.mark.parametrize("magnitude", [0.01, 1.0, 1e6])
    def test_independent_of_pnl_magnitude(self, magnitude: float) -> None:
        """A single trade of any size gives the same annualised Sharpe.

        This is the whole point of the case. The series is one non zero period
        out of T, the sample standard deviation uses denominator T - 1, and the
        annualisation is the naive sqrt(q).
        """
        import numpy as np

        periods_per_year = 252.0
        n_periods = 1260  # five years
        series = np.zeros(n_periods)
        series[400] = magnitude
        sharpe_period = series.mean() / series.std(ddof=1)
        sharpe_annual = sharpe_period * math.sqrt(periods_per_year)
        expected = const.degenerate_annual_sharpe(n_periods / periods_per_year)
        assert sharpe_annual == pytest.approx(expected, rel=1e-12)

    @pytest.mark.parametrize("years", [0.0, -1.0])
    def test_rejects_non_positive_horizon(self, years: float) -> None:
        with pytest.raises(ValueError):
            const.degenerate_annual_sharpe(years)


class TestDilutionRatio:
    """Analytic cases of 02 section 1.6, in both annualisation conventions."""

    def test_full_activity_is_neutral(self) -> None:
        assert const.dilution_ratio_annualised(1.0, 1.5) == pytest.approx(1.0)
        assert const.dilution_ratio_per_period(1.0, 1.5) == pytest.approx(1.0)

    def test_annualised_never_exceeds_one(self) -> None:
        for p in (0.15, 0.4, 0.8, 1.0):
            for s in (0.0, 0.2, 1.0, 3.0):
                assert const.dilution_ratio_annualised(p, s) <= 1.0 + 1e-15

    def test_documented_values(self) -> None:
        assert const.dilution_ratio_annualised(0.25, 0.2) == pytest.approx(0.98533, abs=1e-5)
        assert const.dilution_ratio_annualised(0.10, 1.0) == pytest.approx(0.72548, abs=1e-5)

    def test_the_two_conventions_differ_by_sqrt_p(self) -> None:
        """The whole point of separating them. See 02 section 1.6."""
        for p in (0.15, 0.3, 0.75):
            for s in (0.2, 1.0):
                assert const.dilution_ratio_per_period(p, s) == pytest.approx(
                    math.sqrt(p) * const.dilution_ratio_annualised(p, s)
                )

    def test_both_monotone_in_activity(self) -> None:
        for fn in (const.dilution_ratio_annualised, const.dilution_ratio_per_period):
            values = [fn(p, 1.0) for p in (0.15, 0.3, 0.6, 1.0)]
            assert all(a < b for a, b in zip(values, values[1:], strict=False))

    def test_per_period_form_matches_synthetic_series(self) -> None:
        """Recovery under synthetic data with p and s known by construction.

        The engine computes both Sharpes on the same calendar period length, so
        the per period form is the one the code must reproduce. The annualised
        form is what a practitioner reaches for when quoting the active only
        number, and it is off by exactly sqrt(p).
        """
        import numpy as np

        rng = np.random.default_rng(11)
        n_active, n_total = 3000, 20000
        p = n_active / n_total
        active = rng.normal(0.004, 0.01, n_active)
        s_active = float(active.mean() / active.std(ddof=1))
        grid = np.zeros(n_total)
        grid[:n_active] = active
        s_grid = float(grid.mean() / grid.std(ddof=1))
        assert s_grid / s_active == pytest.approx(
            const.dilution_ratio_per_period(p, s_active), rel=1e-3
        )

    @pytest.mark.parametrize("p", [0.0, -0.1, 1.5])
    def test_rejects_active_fraction_outside_unit_interval(self, p: float) -> None:
        for fn in (const.dilution_ratio_annualised, const.dilution_ratio_per_period):
            with pytest.raises(ValueError):
                fn(p, 1.0)


class TestPnlAtol:
    """Scale invariance and rejection of degenerate arguments."""

    def test_scales_linearly_in_every_argument(self) -> None:
        base = const.pnl_atol(0.25, 50.0, 2.0)
        assert const.pnl_atol(0.5, 50.0, 2.0) == pytest.approx(2 * base)
        assert const.pnl_atol(0.25, 100.0, 2.0) == pytest.approx(2 * base)
        assert const.pnl_atol(0.25, 50.0, 4.0) == pytest.approx(2 * base)

    def test_es_contract_example(self) -> None:
        """ES tick is 0.25 index points, multiplier 50, so one tick is 12.50 per contract."""
        assert const.pnl_atol(0.25, 50.0, 1.0) == pytest.approx(12.5)

    @pytest.mark.parametrize(
        ("tick", "mult", "qty"),
        [(0.0, 50.0, 1.0), (0.25, 0.0, 1.0), (0.25, 50.0, 0.0), (-0.25, 50.0, 1.0)],
    )
    def test_rejects_non_positive_arguments(self, tick: float, mult: float, qty: float) -> None:
        with pytest.raises(ValueError):
            const.pnl_atol(tick, mult, qty)


class TestPeriodRates:
    def test_weekday_rate_is_not_the_equity_session_count(self) -> None:
        """261 weekdays, not 252 sessions. Conflating them biases Sharpe by 1.8 percent."""
        assert pytest.approx(260.893, abs=1e-3) == const.WEEKDAYS_PER_YEAR
        assert math.sqrt(const.WEEKDAYS_PER_YEAR / 252.0) == pytest.approx(1.0175, abs=1e-4)

    def test_nominal_map_covers_the_whole_ladder(self) -> None:
        assert set(const.NOMINAL_PERIODS_PER_YEAR) == set(Period)

    def test_rates_are_ordered_by_grid_coarseness(self) -> None:
        ladder = (Period.DAILY, Period.WEEKLY, Period.MONTHLY)
        rates = [const.NOMINAL_PERIODS_PER_YEAR[p] for p in ladder]
        assert all(a > b for a, b in zip(rates, rates[1:], strict=False))
