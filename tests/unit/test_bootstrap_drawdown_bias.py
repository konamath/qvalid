"""D051. The composition of section 2 and section 5, which nothing checked.

``core/resample.py`` was verified against block length recovery, and
``core/risk.py`` against closed forms for the drawdown. Neither says anything
about what happens when one feeds the other, and that composition is what the
report actually prints.

Measured here and recorded in D051: at zero autocorrelation the simulated
drawdown distribution is exact, and it grows steadily too small as dependence
rises, because the stationary bootstrap breaks dependence at every block join.
The direction is the dangerous one, so the pipeline warns rather than staying
quiet.

The measurement itself is too slow for every run: it needs a conditional null,
which means simulating the true process for each observed series. What is
tested here is the **consequence**: that the warning appears exactly when the
estimated block length says it should, and says which way the error goes.
"""

from __future__ import annotations

import numpy as np
import pytest

from qvalid.contracts import Basis, Period, PeriodReturns
from qvalid.core.constants import WEEKDAYS_PER_YEAR
from qvalid.core.resample import resample_equity_paths
from qvalid.core.risk import drawdown_distribution
from qvalid.pipeline import BLOCK_LENGTH_WARNING_THRESHOLD, _block_bootstrap_warning

DAY_NS = 86_400 * 1_000_000_000
EPOCH = 1_600_000_000 * 1_000_000_000


def periods(values: np.ndarray) -> PeriodReturns:
    values = np.ascontiguousarray(values, dtype=np.float64)
    return PeriodReturns(
        values=values,
        period_end_ns=EPOCH + np.arange(values.size, dtype=np.int64) * DAY_NS,
        period=Period.DAILY,
        periods_per_year=WEEKDAYS_PER_YEAR,
        calendar_id="TEST",
        basis=Basis.FIXED_INITIAL,
        initial_capital=100_000.0,
        n_active=int((values != 0.0).sum()),
    )


def ar1(rho: float, n: int, seed: int, sigma: float = 0.003) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.empty(n, dtype=np.float64)
    out[0] = rng.normal(0.0, sigma / np.sqrt(1.0 - rho * rho))
    for t in range(1, n):
        out[t] = rho * out[t - 1] + rng.normal(0.0, sigma)
    return out


class TestTheWarningTracksTheDependence:
    def test_an_independent_series_gets_no_warning(self) -> None:
        """At block length 1 the measured ratio was 1.001, so silence is correct."""
        assert _block_bootstrap_warning(1.0) == ()
        assert _block_bootstrap_warning(BLOCK_LENGTH_WARNING_THRESHOLD) == ()

    @pytest.mark.parametrize("block_length", [2.01, 4.0, 7.4, 11.3, 40.0])
    def test_a_dependent_series_gets_one(self, block_length: float) -> None:
        warning = _block_bootstrap_warning(block_length)
        assert len(warning) == 1
        assert f"{block_length:.2f}" in warning[0]

    def test_the_warning_states_the_direction(self) -> None:
        """A warning that does not say which way the error goes is decoration.

        Understated, not merely uncertain: the reader is deciding how much to
        risk, and the two words imply opposite actions.
        """
        text = _block_bootstrap_warning(5.0)[0]
        assert "understated" in text
        assert "lower bound" in text

    def test_a_serially_dependent_series_crosses_the_threshold_in_practice(self) -> None:
        """The threshold has to fire on real input, not only on a passed number."""
        estimate = resample_equity_paths(periods(ar1(0.3, 750, seed=11)), n_paths=50, seed=3)
        assert estimate.block_length.block_length > BLOCK_LENGTH_WARNING_THRESHOLD
        assert _block_bootstrap_warning(estimate.block_length.block_length) != ()

    def test_an_independent_series_stays_below_it_in_practice(self) -> None:
        estimate = resample_equity_paths(periods(ar1(0.0, 750, seed=13)), n_paths=50, seed=3)
        assert estimate.block_length.block_length <= BLOCK_LENGTH_WARNING_THRESHOLD


class TestTheBiasItselfAtZeroDependence:
    """The one point of the curve cheap enough to check on every run."""

    def test_the_simulated_distribution_is_right_when_there_is_no_dependence(self) -> None:
        """Ratio measured at 1.0012 with a standard error of 0.0037 over 24 series.

        One series here, so the tolerance is the sampling error of a single
        draw rather than of the mean. It still fails if the composition breaks:
        the earlier measurement of a fifty per cent gap turned out to be a
        badly built comparison, and this is the assertion that would have said
        so immediately.
        """
        observed = ar1(0.0, 750, seed=17)
        simulated = resample_equity_paths(periods(observed), n_paths=3000, seed=5)
        distribution = drawdown_distribution(simulated.paths, seed=11)

        mean, deviation = float(observed.mean()), float(observed.std(ddof=1))
        rng = np.random.default_rng(23)
        draws = rng.normal(mean, deviation, size=(3000, observed.size))
        equity = 1.0 + np.cumsum(draws, axis=1)
        truth = np.max(1.0 - equity / np.maximum.accumulate(equity, axis=1), axis=1)

        assert distribution.quantiles[0.5] == pytest.approx(
            float(np.quantile(truth, 0.5)), rel=0.05
        )
