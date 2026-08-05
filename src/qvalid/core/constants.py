"""Named constants that participate in statistical decisions.

Rule from ``04_convencoes_de_codigo.md``: a constant without a written
derivation is a magic number with a name. The requirement is not the name, it
is the verifiable justification.

This module enforces that rule two ways. Every public constant has an entry in
:data:`DERIVATIONS`, checked by a test, and the two derivations that reduce to
closed form are exposed as functions, :func:`sparsity_kurtosis` and
:func:`degenerate_annual_sharpe`, so the justification is executed rather than
merely asserted in prose.

References
----------
Newey, W. K., and West, K. D. (1987). A simple, positive semi-definite,
heteroskedasticity and autocorrelation consistent covariance matrix.
Econometrica 55(3), 703-708.

Newey, W. K., and West, K. D. (1994). Automatic lag selection in covariance
matrix estimation. Review of Economic Studies 61(4), 631-653.

Politis, D. N., and White, H. (2004). Automatic block-length selection for the
dependent bootstrap. Econometric Reviews 23(1), 53-70.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from qvalid.contracts import Period

__all__ = [
    "BARRIER_CONTINUITY_CORRECTION",
    "DEFAULT_CONFIDENCE_LEVEL",
    "DERIVATIONS",
    "EULER_MASCHERONI",
    "EXPECTED_MAX_DRAWDOWN_COEFFICIENT",
    "MAX_HOLDING_TO_PERIOD",
    "MIN_ACTIVE_FRACTION",
    "MIN_BLOCK_SAMPLE_RATIO",
    "MIN_PERIODS",
    "MIN_STATE_OBS",
    "MIN_TRADES",
    "MONTHS_PER_YEAR",
    "NOMINAL_PERIODS_PER_YEAR",
    "NW_BARTLETT_BANDWIDTH_CONSTANT",
    "NW_LAG_SELECTION_COEFFICIENT",
    "NW_LAG_SELECTION_EXPONENT",
    "PNL_RTOL",
    "SPARSITY_KURTOSIS_ROOT",
    "WEEKDAYS_PER_YEAR",
    "WEEKS_PER_YEAR",
    "degenerate_annual_sharpe",
    "dilution_ratio_annualised",
    "dilution_ratio_per_period",
    "pnl_atol",
    "sparsity_kurtosis",
]

MIN_TRADES: Final[int] = 30
"""Minimum number of closed trades for asymptotic inference to be reported."""

MIN_PERIODS: Final[int] = 60
"""Minimum number of calendar periods on the grid."""

MIN_ACTIVE_FRACTION: Final[float] = 0.15
"""Minimum fraction of grid periods holding at least one attributed trade."""

MAX_HOLDING_TO_PERIOD: Final[float] = 1.0
"""Maximum ratio of median holding duration to grid period length."""

PNL_RTOL: Final[float] = 1e-6
"""Relative tolerance of the P&L coherence identity."""

MIN_BLOCK_SAMPLE_RATIO: Final[float] = 10.0
"""Minimum ratio of sample size to estimated bootstrap block length."""

MIN_STATE_OBS: Final[int] = 20
"""Minimum observations per regime state before a transition matrix is estimated."""

EULER_MASCHERONI: Final[float] = 0.5772156649015329
"""Euler-Mascheroni constant, the weight of the two extreme value quantiles."""

BARRIER_CONTINUITY_CORRECTION: Final[float] = 0.5826
"""Shift of a discretely monitored barrier, in standard deviations of one step."""

EXPECTED_MAX_DRAWDOWN_COEFFICIENT: Final[float] = math.sqrt(math.pi / 2.0)
"""Coefficient of the expected maximum drawdown of driftless Brownian motion."""

NW_LAG_SELECTION_COEFFICIENT: Final[float] = 4.0
"""Coefficient of the Newey and West (1994) lag selection parameter for Bartlett."""

NW_LAG_SELECTION_EXPONENT: Final[float] = 2.0 / 9.0
"""Exponent of the Newey and West (1994) lag selection parameter for Bartlett."""

NW_BARTLETT_BANDWIDTH_CONSTANT: Final[float] = 1.1447
"""Kernel constant of the Newey and West (1994) plug in bandwidth for Bartlett."""

DEFAULT_CONFIDENCE_LEVEL: Final[float] = 0.95
"""Two sided level of every reported interval, unless overridden and reported."""

SPARSITY_KURTOSIS_ROOT: Final[float] = (9.0 - math.sqrt(45.0)) / 18.0
"""Active fraction at which sparsity alone manufactures a kurtosis of exactly 6."""

WEEKDAYS_PER_YEAR: Final[float] = 365.25 * 5.0 / 7.0
"""Weekdays per Julian year, the nominal rate of the ``WEEKDAYS_UTC`` sentinel."""

WEEKS_PER_YEAR: Final[float] = 365.25 / 7.0
"""Weeks per Julian year."""

MONTHS_PER_YEAR: Final[float] = 12.0
"""Months per year, exact by definition of the calendar."""

NOMINAL_PERIODS_PER_YEAR: Final[Mapping[Period, float]] = {
    Period.DAILY: WEEKDAYS_PER_YEAR,
    Period.WEEKLY: WEEKS_PER_YEAR,
    Period.MONTHLY: MONTHS_PER_YEAR,
}
"""Fallback period rates for the ``WEEKDAYS_UTC`` sentinel calendar.

A real exchange calendar overrides these with its observed session rate, via
``TradingCalendar.sessions_per_year``. The value actually used enters the
``ValidationReport``, so neither path is a silent default.
"""

DERIVATIONS: Final[Mapping[str, str]] = {
    "MIN_TRADES": (
        "Conventional threshold for asymptotic inference. Below it the sample "
        "moments that enter the Sharpe standard error, in particular skewness "
        "and kurtosis, are too noisy to correct anything. Metrics are still "
        "reported, with a warning, but the overfitting and regime sections are "
        "suppressed. See 02 section 1.4."
    ),
    "MIN_PERIODS": (
        "The automatic Newey and West (1994) bandwidth scales as "
        "4 * (T/100)^(2/9). Requiring at least 15 observations per retained "
        "lag, T = 60 gives L close to 4 and a ratio of 15. Below that the HAC "
        "long run variance estimator is noise. See 02 section 1.4."
    ),
    "MIN_ACTIVE_FRACTION": (
        "For a series taking a single non zero value with frequency p, the "
        "kurtosis is exactly (1 - 3p + 3p^2) / (p * (1 - p)). It is "
        "manufactured by sparsity, not by the return distribution. The root of "
        "9p^2 - 9p + 1 = 0 at p = 0.1273 gives kurtosis 6, that is excess 3, "
        "the order of magnitude of the excess kurtosis of daily index returns. "
        "At p = 0.1464 the induced excess falls to 2. The threshold of 0.15 "
        "sits just above, with induced excess 1.84, so the fourth moment "
        "entering the Sharpe standard error is not dominated by the artefact. "
        "See 02 section 1.4 and sparsity_kurtosis."
    ),
    "MAX_HOLDING_TO_PERIOD": (
        "P&L is attributed to the period containing exit_ts. If the median "
        "holding duration exceeds the period length, the attribution clusters "
        "several periods of exposure into one period of realisation, which "
        "distorts the estimated autocorrelation and therefore the annualisation "
        "factor. See 02 section 1.4."
    ),
    "PNL_RTOL": (
        "Floating point precision and currency conversion residue. Set well "
        "above double precision epsilon and well below any economically "
        "meaningful discrepancy, so it never masks a real multiplier error. "
        "The absolute floor is not a constant, see pnl_atol."
    ),
    "MIN_BLOCK_SAMPLE_RATIO": (
        "An estimated expected block length above sample size divided by 10 "
        "leaves fewer than ten effectively independent blocks, so the bootstrap "
        "distribution is driven by a handful of resampled segments and its "
        "coverage claim is void. See 02 section 2.1."
    ),
    "MIN_STATE_OBS": (
        "Below 20 observations in a regime state, the estimated row of the "
        "transition matrix has a standard error of the same order as its "
        "entries, and conditional resampling within the state degenerates into "
        "repetition of a few observations. See 02 section 2.2."
    ),
    "EULER_MASCHERONI": (
        "gamma = 0.5772156649, the limit of the harmonic series minus its "
        "logarithm. It appears in the expected maximum of N independent normal "
        "draws, which Bailey and Lopez de Prado (2014) use as the threshold a "
        "strategy must clear merely to be the best of N trials. It is a "
        "mathematical constant rather than a project choice, and the "
        "approximation it belongs to is asymptotic in N: measured against "
        "simulation the closed form is 6 per cent off at N = 2 and under 1 per "
        "cent from N = 200. See D024."
    ),
    "BARRIER_CONTINUITY_CORRECTION": (
        "beta = -zeta(1/2) / sqrt(2*pi) = 0.5826, from Broadie, Glasserman and "
        "Kou (1997). A barrier monitored at discrete times is crossed less often "
        "than a continuously monitored one, because the path can dip below and "
        "return between two observations. Shifting the barrier outward by "
        "beta * sigma * sqrt(dt) makes the continuous closed form reproduce the "
        "discrete probability. Measured on 300000 paths, the uncorrected form is "
        "off by 10 to 43 Monte Carlo standard errors and the corrected form by at "
        "most 1.3. See D022."
    ),
    "EXPECTED_MAX_DRAWDOWN_COEFFICIENT": (
        "sqrt(pi/2) = 1.2533, the coefficient in E[MDD] = sqrt(pi/2) * sigma * "
        "sqrt(T) for driftless Brownian motion, from Magdon-Ismail, Atiya, Pratap "
        "and Abu-Mostafa (2004). Under discrete monitoring the same continuity "
        "correction applies twice, once for the running maximum and once for the "
        "current level, giving E[MDD] - 2 * beta * sigma. Measured ratios of "
        "simulation to that form: 0.996 at T = 60 and 1.0003 at T = 4000."
    ),
    "NW_LAG_SELECTION_COEFFICIENT": (
        "With NW_LAG_SELECTION_EXPONENT, forms n = floor(4 * (T/100)^(2/9)), the "
        "lag selection parameter of Newey and West (1994) for the Bartlett "
        "kernel. Note that n is not the bandwidth: it is the number of "
        "autocovariances that feed the plug in estimate of the optimal "
        "bandwidth, which is typically several times larger. The derivation of "
        "MIN_PERIODS in 02 section 1.4 is anchored on this parameter, not on the "
        "bandwidth it produces."
    ),
    "NW_LAG_SELECTION_EXPONENT": (
        "Exponent 2/9 of the Newey and West (1994) lag selection parameter for "
        "the Bartlett kernel, whose characteristic exponent is 1. It comes from "
        "the rate at which the number of autocovariances needed for the plug in "
        "estimate must grow with T for the bandwidth to be consistent."
    ),
    "NW_BARTLETT_BANDWIDTH_CONSTANT": (
        "Kernel dependent constant of the Newey and West (1994) plug in "
        "bandwidth, L = floor(1.1447 * (alpha * T)^(1/3)) for Bartlett. Tabulated "
        "by the authors from the kernel's own moments; it is a property of the "
        "Bartlett weights, not a project choice, and changing kernel changes it."
    ),
    "DEFAULT_CONFIDENCE_LEVEL": (
        "Two sided 95 per cent, the convention every reader calibrates against. "
        "It is a default, not a silent one: the level actually used is carried on "
        "SharpeEstimate and enters the ValidationReport, per the prohibition in "
        "04 on statistical parameters with silent defaults."
    ),
    "SPARSITY_KURTOSIS_ROOT": (
        "Smaller root of 9p^2 - 9p + 1 = 0, the active fraction at which "
        "sparsity induced kurtosis equals 6. Exposed as a constant because it "
        "is the anchor of the MIN_ACTIVE_FRACTION derivation and is asserted "
        "directly in the test suite."
    ),
    "WEEKDAYS_PER_YEAR": (
        "365.25 * 5 / 7 = 260.89. This is the rate of the WEEKDAYS_UTC "
        "sentinel calendar, not of any exchange. It is deliberately not 252: "
        "252 counts US equity sessions net of holidays, and using it with a "
        "sentinel calendar that includes holidays would understate the "
        "annualisation factor by a factor of sqrt(261/252), about 1.8 percent "
        "on the Sharpe ratio. A real calendar supplies its own observed rate."
    ),
    "WEEKS_PER_YEAR": "365.25 / 7 = 52.18, Julian year in weeks.",
    "MONTHS_PER_YEAR": "Exact by definition of the Gregorian calendar.",
    "NOMINAL_PERIODS_PER_YEAR": (
        "Fallback map used only with the WEEKDAYS_UTC sentinel. Overridden by "
        "the observed session rate of a materialised exchange calendar. The "
        "value used enters the ValidationReport either way."
    ),
}


def sparsity_kurtosis(p: float) -> float:
    """Kurtosis induced purely by sparsity of a spike series.

    Parameters
    ----------
    p : float
        Fraction of periods holding the single non zero value, in ``(0, 1)``.

    Returns
    -------
    float
        Kurtosis, not excess kurtosis.

    Notes
    -----
    For a series equal to ``x`` with probability ``p`` and zero otherwise, the
    central moments are ``m2 = p * x^2 * (1 - p)`` and
    ``m4 = p * x^4 * (1 - p) * ((1 - p)^3 + p^3)``, so

    ``kurtosis = ((1 - p)^3 + p^3) / (p * (1 - p)) = (1 - 3p + 3p^2) / (p * (1 - p))``

    independently of ``x``. The magnitude of the P&L cancels: what is left is
    an artefact of how often the strategy is in the market. This is the reason
    ``MIN_ACTIVE_FRACTION`` exists, because the fourth moment feeds the Sharpe
    standard error of ``02`` section 1.3.

    The function is symmetric about ``p = 0.5``, where it attains its minimum
    of 1, the kurtosis of a symmetric Bernoulli variable.

    Raises
    ------
    ValueError
        If ``p`` is outside the open unit interval. This is a programming
        error, not a foreseen data condition, so a typed exception would be
        misleading here.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must lie in the open interval (0, 1), got {p}")
    return (1.0 - 3.0 * p + 3.0 * p * p) / (p * (1.0 - p))


def dilution_ratio_per_period(active_fraction: float, active_sharpe: float) -> float:
    """Per period dilution: grid Sharpe over active only Sharpe, same period length.

    Parameters
    ----------
    active_fraction : float
        Fraction of grid periods holding at least one trade, in ``(0, 1]``.
    active_sharpe : float
        Per period Sharpe ratio computed over the active periods alone.

    Returns
    -------
    float
        ``sqrt(p) / sqrt(1 + (1 - p) * s^2)``.

    Notes
    -----
    Derivation. Let the active periods have mean ``mu_a`` and standard
    deviation ``sigma_a``, and let the remaining periods be exactly zero. Then

    ``mean_grid = p * mu_a``

    ``var_grid  = p * sigma_a^2 + p * (1 - p) * mu_a^2``

    so the per period grid Sharpe is
    ``sqrt(p) * s / sqrt(1 + (1 - p) * s^2)`` with ``s = mu_a / sigma_a``.
    Dividing by ``s`` gives the expression above.

    The ``sqrt(p)`` factor is the part that a per period comparison keeps and an
    annualised comparison cancels. See :func:`dilution_ratio_annualised` for
    why, and ``02`` section 1.6 for the acceptance test that separates them.
    """
    _check_active_fraction(active_fraction)
    return math.sqrt(active_fraction) / math.sqrt(
        1.0 + (1.0 - active_fraction) * active_sharpe * active_sharpe
    )


def dilution_ratio_annualised(active_fraction: float, active_sharpe: float) -> float:
    """Annualised dilution, each series scaled by its own arrival rate.

    Parameters
    ----------
    active_fraction : float
        Fraction of grid periods holding at least one trade, in ``(0, 1]``.
    active_sharpe : float
        Per period Sharpe ratio computed over the active periods alone.

    Returns
    -------
    float
        ``1 / sqrt(1 + (1 - p) * s^2)``, always at most 1.

    Notes
    -----
    This is the comparison a practitioner makes without noticing. The grid
    series is annualised by ``sqrt(q)``; the active only series, treated as a
    series in its own right, is annualised by ``sqrt(p * q)``, because only
    ``p * q`` of its periods occur per year. The ratio of the two annualisation
    factors is ``1 / sqrt(p)``, which cancels the ``sqrt(p)`` of
    :func:`dilution_ratio_per_period`.

    The grid figure is the correct one, because capital parked between trades
    is still capital allocated to the strategy. This function quantifies
    exactly how much a report quoting the active only Sharpe overstates.

    Numerically, ``p = 0.25`` and ``s = 0.2`` give 0.985, an overstatement of
    1.5 percent; ``p = 0.10`` and ``s = 1.0`` give 0.725, an overstatement of
    38 percent. Sparse strategies with a strong per trade edge are where the
    error is large, which is exactly the population that reports the active
    only number.
    """
    _check_active_fraction(active_fraction)
    return 1.0 / math.sqrt(1.0 + (1.0 - active_fraction) * active_sharpe * active_sharpe)


def _check_active_fraction(active_fraction: float) -> None:
    if not 0.0 < active_fraction <= 1.0:
        raise ValueError(f"active_fraction must lie in (0, 1], got {active_fraction}")


def degenerate_annual_sharpe(years: float) -> float:
    """Annualised Sharpe of a grid holding exactly one non zero period.

    Parameters
    ----------
    years : float
        Sample length in years, that is ``n_periods / periods_per_year``.

    Returns
    -------
    float
        ``1 / sqrt(years)``.

    Notes
    -----
    With one non zero period out of ``T`` and the sample standard deviation
    taken with denominator ``T - 1``, the algebra collapses: the mean is
    ``x / T`` and the standard deviation is exactly ``x / sqrt(T)``, so the per
    period Sharpe is ``1 / sqrt(T)`` and the ``sqrt(q)`` annualisation gives
    ``sqrt(q / T) = 1 / sqrt(Y)``. The magnitude ``x`` cancels completely.

    A single trade that multiplied capital tenfold over five years and one that
    made a cent over five years both report 0.447. This is the case that
    justifies ``MIN_ACTIVE_FRACTION``: in that regime the statistic measures
    period count, not performance.
    """
    if years <= 0.0:
        raise ValueError(f"years must be positive, got {years}")
    return 1.0 / math.sqrt(years)


def pnl_atol(tick_size: float, multiplier: float, qty: float) -> float:
    """Absolute floor of the P&L coherence tolerance.

    Parameters
    ----------
    tick_size : float
        Minimum price increment of the instrument, from the symbology map.
    multiplier : float
        Contract multiplier. 1 for equities and crypto.
    qty : float
        Traded quantity.

    Returns
    -------
    float
        ``tick_size * multiplier * qty``.

    Notes
    -----
    Not a module constant, because it depends on instrument and trade. The
    derivation is half a tick of price rounding on each leg, entry and exit,
    which sums to one full tick, converted to account currency by multiplier
    and quantity. See ``01`` for the identity and D007 for why the check runs
    at the adapter boundary, where tick size is available.
    """
    if tick_size <= 0.0 or multiplier <= 0.0 or qty <= 0.0:
        raise ValueError(
            f"tick_size, multiplier and qty must all be positive, got "
            f"{tick_size}, {multiplier}, {qty}"
        )
    return tick_size * multiplier * qty
