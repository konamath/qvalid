"""Descriptive performance statistics, split by index unit.

Two entry points with deliberately disjoint signatures:

:func:`trade_metrics`
    Takes :class:`~qvalid.contracts.TradeReturns`. Produces expectancy, hit rate,
    profit factor and the shape of the per trade P&L distribution. Nothing here
    carries a time unit and nothing here is annualised. See D006.

:func:`period_metrics`
    Takes :class:`~qvalid.contracts.PeriodReturns`. The only source of cumulative
    return, CAGR, annualised volatility, Sharpe, Sortino, drawdown, time
    underwater and the Kelly fraction.

There is no conversion path between them, and the prohibition is structural
rather than a review convention: an annualising function needs
``periods_per_year``, and ``TradeReturns`` does not have it.

Degrees of freedom, declared because ``02`` uses both conventions
-----------------------------------------------------------------
Reported point estimates of dispersion, that is volatility, Sharpe, Sortino and
the Kelly fraction, use the sample standard deviation with denominator ``T-1``.
That is the practitioner convention and it is the one under which the
degenerate acceptance case of ``02`` section 1.6 holds exactly, giving
``1/sqrt(T)`` for a grid with a single non zero period.

The delta method of ``02`` section 1.3 uses population moments internally,
denominator ``T``, because its derivatives are taken with respect to ``E[r]``
and ``E[r^2]``, and because the reduction to Mertens (2002) is an exact
algebraic identity under that convention rather than an asymptotic statement.
The dilution identity of section 1.6 is likewise exact only under denominator
``T``.

The two conventions differ by the factor ``sqrt(T / (T-1))``, which is 0.85 per
cent at ``MIN_PERIODS`` and falls as ``1/(2T)``. Both are reported, so nothing
is hidden, and :func:`sharpe_ratio` exposes the population form as
``per_period_population`` next to the sample form.

References
----------
Newey, W. K., and West, K. D. (1987). A simple, positive semi-definite,
heteroskedasticity and autocorrelation consistent covariance matrix.
Econometrica 55(3), 703-708.

Newey, W. K., and West, K. D. (1994). Automatic lag selection in covariance
matrix estimation. Review of Economic Studies 61(4), 631-653.

Lo, A. W. (2002). The statistics of Sharpe ratios. Financial Analysts Journal
58(4), 36-52.

Mertens, E. (2002). Comments on variance of the IID estimator in Lo (2002).
Working paper.

Christie, S. (2005). Is the Sharpe ratio useful in asset allocation? MAFC
Research Paper 31, Macquarie University.

Opdyke, J. D. (2007). Comparing Sharpe ratios: so where are the p-values?
Journal of Asset Management 8(5), 308-336.

Sortino, F. A., and Price, L. N. (1994). Performance measurement in a downside
risk framework. Journal of Investing 3(3), 59-64.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from qvalid.contracts import Basis, FloatArray, Period, PeriodReturns, TradeReturns
from qvalid.core.constants import (
    DEFAULT_CONFIDENCE_LEVEL,
    MIN_PERIODS,
    MIN_TRADES,
    NW_BARTLETT_BANDWIDTH_CONSTANT,
    NW_LAG_SELECTION_COEFFICIENT,
    NW_LAG_SELECTION_EXPONENT,
)
from qvalid.exceptions import InsufficientSampleError

__all__ = [
    "DrawdownProfile",
    "PeriodMetrics",
    "SharpeEstimate",
    "TradeMetrics",
    "bartlett_long_run_covariance",
    "bartlett_long_run_variance",
    "de_annualise_rate",
    "dispersion_is_negligible",
    "drawdown_profile",
    "equity_curve",
    "inner_product",
    "mertens_sharpe_variance",
    "newey_west_bandwidth",
    "newey_west_lag_selection",
    "period_metrics",
    "quadratic_form",
    "sharpe_ratio",
    "trade_metrics",
]


def inner_product(left: FloatArray, right: FloatArray) -> float:
    """Inner product whose result does not depend on the BLAS configuration.

    ``left @ right`` dispatches to the BLAS, which splits the reduction across
    threads once the input is large enough. Splitting changes the order of the
    summation, so the same input produces different last digits on machines
    with different core counts, and the byte equality of D030 stops holding on
    a long series. Measured on OpenBLAS 0.3.29: identical up to ten thousand
    elements, divergent from a hundred thousand.

    ``numpy.sum`` of the elementwise product uses pairwise summation, which is
    single threaded and whose order depends only on the length of the input. It
    is also the most accurate of the three candidates, measured against
    ``math.fsum`` over ten million elements: relative error 1.4e-16, against
    2.3e-15 for the BLAS and 1.1e-15 for ``numpy.einsum``. The temporary array
    it allocates is the price, and it is why this is not free on memory.

    See D041.

    Parameters
    ----------
    left, right : numpy.ndarray of float64
        Same shape.

    Returns
    -------
    float
    """
    return float(np.sum(left * right))


def quadratic_form(vector: FloatArray, matrix: FloatArray) -> float:
    """``v @ M @ v``, built from sums for the reason in :func:`inner_product`.

    Parameters
    ----------
    vector : numpy.ndarray of float64, shape ``(k,)``
    matrix : numpy.ndarray of float64, shape ``(k, k)``

    Returns
    -------
    float
    """
    return float(np.sum(vector * np.sum(matrix * vector, axis=1)))


def _cross_moment_matrix(left: FloatArray, right: FloatArray) -> FloatArray:
    """``left.T @ right`` assembled from :func:`inner_product`, same reason.

    The loop is over ``k``, the number of columns, which is two for the delta
    method of ``02`` section 1.3. The reduction that could be split across
    threads is the one over ``T``, and that is the one this removes.
    """
    n_left = left.shape[1]
    n_right = right.shape[1]
    out = np.empty((n_left, n_right), dtype=np.float64)
    for i in range(n_left):
        for j in range(n_right):
            out[i, j] = inner_product(left[:, i], right[:, j])
    return out


def _autocovariance(centred: FloatArray, lag: int) -> float:
    """Biased autocovariance at ``lag``, denominator ``T``.

    Denominator ``T`` rather than ``T - lag`` is what makes the Bartlett
    weighted sum positive semidefinite by construction, which is the whole
    point of Newey and West (1987).
    """
    if lag == 0:
        return inner_product(centred, centred) / centred.size
    return inner_product(centred[lag:], centred[:-lag]) / centred.size


def newey_west_lag_selection(n_obs: int) -> int:
    """Lag selection parameter of Newey and West (1994) for the Bartlett kernel.

    Parameters
    ----------
    n_obs : int
        Sample size.

    Returns
    -------
    int
        ``floor(4 * (T / 100) ** (2/9))``, floored at 1.

    Notes
    -----
    This is the quantity that ``02`` section 1.4 cites when deriving
    ``MIN_PERIODS``. It is **not** the bandwidth: it is the number of
    autocovariances that feed the plug in estimate of the optimal bandwidth.
    The bandwidth itself comes out of :func:`newey_west_bandwidth` and is
    typically several times larger. The requirement of roughly fifteen
    observations per retained lag applies to this parameter, and at ``T = 60``
    it returns 3, so the ratio is 20.
    """
    if n_obs < 1:
        raise InsufficientSampleError(
            "lag selection needs at least one observation", observed=n_obs, threshold=1
        )
    raw = NW_LAG_SELECTION_COEFFICIENT * (n_obs / 100.0) ** NW_LAG_SELECTION_EXPONENT
    return max(int(raw), 1)


def newey_west_bandwidth(values: FloatArray) -> int:
    """Select the Bartlett bandwidth by the data dependent rule of Newey and West (1994).

    Parameters
    ----------
    values : numpy.ndarray of float64
        Series whose long run variance is to be estimated. Demeaned internally.

    Returns
    -------
    int
        Bandwidth ``L``, in ``[0, T - 1]``.

    Notes
    -----
    Plug in rule for the Bartlett kernel, whose characteristic exponent is 1:

    ``s0 = sigma_0 + 2 * sum_{j=1..n} sigma_j``

    ``s1 = 2 * sum_{j=1..n} j * sigma_j``

    ``alpha = 4 * (s1 / s0)^2``

    ``L = floor(1.1447 * (alpha * T)^(1/3))``

    with ``n`` from :func:`newey_west_lag_selection`. A degenerate ``s0`` of
    zero, which happens for a constant series, returns a bandwidth of zero,
    reducing the estimator to the sample variance. That is the right limit: a
    series with no variation has no serial dependence to correct for.

    Hypotheses. Weak stationarity and short range dependence. Under a
    structural break the estimator mixes regimes and the bandwidth is
    meaningless, which is the condition ``02`` section 1.4 lists last and which
    ``core/regimes.py`` exists to detect.
    """
    n_obs = int(values.size)
    if n_obs < 2:
        raise InsufficientSampleError(
            "bandwidth selection needs at least two observations", observed=n_obs, threshold=2
        )
    centred = np.ascontiguousarray(values - values.mean(), dtype=np.float64)
    n_lags = min(newey_west_lag_selection(n_obs), n_obs - 1)
    sigma = np.array([_autocovariance(centred, j) for j in range(n_lags + 1)])
    s0 = sigma[0] + 2.0 * sigma[1:].sum()
    s1 = 2.0 * sum(j * sigma[j] for j in range(1, n_lags + 1))
    if s0 == 0.0:
        return 0
    alpha = 4.0 * (s1 / s0) ** 2
    bandwidth = int(NW_BARTLETT_BANDWIDTH_CONSTANT * (alpha * n_obs) ** (1.0 / 3.0))
    return int(min(max(bandwidth, 0), n_obs - 1))


def bartlett_long_run_variance(values: FloatArray, bandwidth: int) -> float:
    """Long run variance by Newey and West (1987) with the Bartlett kernel.

    Parameters
    ----------
    values : numpy.ndarray of float64
    bandwidth : int
        Truncation lag ``L``. Zero reduces to the population variance.

    Returns
    -------
    float
        ``gamma_0 + 2 * sum_{k=1..L} (1 - k/(L+1)) * gamma_k``.

    Notes
    -----
    The Bartlett weights make the estimate positive semidefinite by
    construction, so a negative long run variance is impossible and no
    truncation guard is needed.

    Finite sample behaviour, measured rather than assumed. The estimator is
    biased downward, so the ratio ``sigma / sigma_LR`` is biased toward one and
    the correction is understated. Under AR(1) with coefficient 0.4 the bias of
    that ratio is 6.7 per cent at ``T = 500`` and 1.9 per cent at ``T = 8000``,
    decreasing at roughly ``T^(-1/3)`` as the theory for this kernel predicts.
    The direction is conservative for a positively autocorrelated strategy: the
    reported Sharpe stays closer to the naive one than the truth warrants.
    """
    n_obs = int(values.size)
    if n_obs < 2:
        raise InsufficientSampleError(
            "long run variance needs at least two observations", observed=n_obs, threshold=2
        )
    if not 0 <= bandwidth <= n_obs - 1:
        raise InsufficientSampleError(
            "bandwidth must lie in [0, T-1]", observed=bandwidth, threshold=n_obs - 1
        )
    centred = np.ascontiguousarray(values - values.mean(), dtype=np.float64)
    total = _autocovariance(centred, 0)
    for lag in range(1, bandwidth + 1):
        total += 2.0 * (1.0 - lag / (bandwidth + 1.0)) * _autocovariance(centred, lag)
    return float(total)


def bartlett_long_run_covariance(matrix: FloatArray, bandwidth: int) -> FloatArray:
    """Long run covariance matrix, same kernel and same weights as the scalar case.

    Parameters
    ----------
    matrix : numpy.ndarray of float64, shape ``(T, k)``
    bandwidth : int

    Returns
    -------
    numpy.ndarray of float64, shape ``(k, k)``
        Symmetric by construction, because the cross lag terms are added
        together with their transposes.
    """
    n_obs, _ = matrix.shape
    if n_obs < 2:
        raise InsufficientSampleError(
            "long run covariance needs at least two observations", observed=n_obs, threshold=2
        )
    centred = np.ascontiguousarray(matrix - matrix.mean(axis=0), dtype=np.float64)
    omega = _cross_moment_matrix(centred, centred) / n_obs
    for lag in range(1, bandwidth + 1):
        cross = _cross_moment_matrix(centred[lag:], centred[:-lag]) / n_obs
        omega += (1.0 - lag / (bandwidth + 1.0)) * (cross + cross.T)
    return omega


def mertens_sharpe_variance(excess: FloatArray) -> float:
    """Variance of the per period Sharpe estimator under independence.

    Parameters
    ----------
    excess : numpy.ndarray of float64
        Excess returns.

    Returns
    -------
    float
        ``(1 + SR^2/2 - g3 * SR + (g4 - 3)/4 * SR^2) / T`` with ``g3`` the
        skewness and ``g4`` the kurtosis, not the excess kurtosis.

    Notes
    -----
    Mertens (2002), restated by Christie (2005) and Opdyke (2007). Used only as
    a consistency check on the general delta method form, per ``02`` section
    1.3. It is not merely the asymptotic limit of that form: with population
    moments and bandwidth zero the two expressions are the *same number* for
    any finite sample, which makes the consistency test exact rather than
    statistical.
    """
    n_obs = int(excess.size)
    if n_obs < 2:
        raise InsufficientSampleError(
            "Mertens variance needs at least two observations", observed=n_obs, threshold=2
        )
    mean = float(excess.mean())
    sigma = float(excess.std(ddof=0))
    if dispersion_is_negligible(excess):
        raise InsufficientSampleError(
            "Mertens variance is undefined for a series of zero dispersion",
            observed=0.0,
            threshold=0.0,
        )
    sharpe = mean / sigma
    centred = excess - mean
    skew = float((centred**3).mean()) / sigma**3
    kurt = float((centred**4).mean()) / sigma**4
    return (
        1.0 + sharpe * sharpe / 2.0 - skew * sharpe + (kurt - 3.0) / 4.0 * sharpe * sharpe
    ) / n_obs


def dispersion_is_negligible(values: FloatArray) -> bool:
    """Decide whether a series has no dispersion at double precision.

    Public because ``core/resample.py`` needs the same test: the block length
    estimator divides by the autocovariance at lag zero, which is the same
    quantity under a different name.

    Testing ``variance == 0.0`` is not enough and fails in practice. For
    ``numpy.full(200, 0.001)`` the elements are identical but the computed
    variance is about ``4.7e-38`` rather than zero, because the sample mean
    carries rounding from the summation. The resulting Sharpe ratio is
    ``7.4e16``, which is the infinity that ``02`` section 1.6 forbids, wearing
    a finite disguise.

    The rule used here is that the dispersion is not distinguishable from zero
    when the standard deviation falls below the error bound of the variance
    computation itself, ``T * eps * max|x|``, which is the standard bound for
    the two pass algorithm. It introduces no free parameter: the multiplier is
    the sample size and the unit is machine epsilon.

    Margins are wide in both directions. For the constant series above the
    floor is ``4.4e-17`` against a computed deviation of ``2.2e-19``. For a
    daily series with a deviation of one per cent the floor is around
    ``3.6e-15``, thirteen orders of magnitude below, so a real series is never
    caught.
    """
    if float(np.ptp(values)) == 0.0:
        return True
    floor = values.size * float(np.finfo(np.float64).eps) * float(np.abs(values).max())
    return float(values.std(ddof=0)) <= floor


def de_annualise_rate(annual_rate: float, periods_per_year: float) -> float:
    """Convert an annual simple rate to the per period rate of the grid.

    Parameters
    ----------
    annual_rate : float
        Simple annual rate, for example 0.045 for four and a half per cent.
    periods_per_year : float

    Returns
    -------
    float
        ``(1 + annual_rate) ** (1 / periods_per_year) - 1``.

    Notes
    -----
    Geometric, not ``annual_rate / periods_per_year``. The linear form makes
    the risk free leg compound to more than the quoted annual rate, and the
    error, although second order, enters the numerator of the Sharpe ratio
    directly where the numerator is itself small. Both the annual input and
    the derived per period value are carried on :class:`SharpeEstimate`, so the
    convention is visible in the report rather than buried here.
    """
    if annual_rate <= -1.0:
        raise InsufficientSampleError(
            "annual_rate must exceed -1 for the geometric conversion to be defined",
            observed=annual_rate,
            threshold=-1.0,
        )
    return float((1.0 + annual_rate) ** (1.0 / periods_per_year) - 1.0)


@dataclass(frozen=True, slots=True)
class SharpeEstimate:
    """Sharpe ratio on both scalings, with the interval and its inputs.

    Attributes
    ----------
    per_period_sample : float or None
        ``mean / std`` with denominator ``T - 1``. ``None`` when the dispersion
        is exactly zero, because the ratio is undefined there, not infinite.
    per_period_population : float or None
        Same ratio with denominator ``T``. The quantity the delta method
        differentiates.
    annualised_sqrt_q : float or None
        ``sqrt(periods_per_year) * per_period_sample``. The naive scaling.
    annualised_hac : float or None
        ``sqrt(periods_per_year) * mean / sigma_LR``. The scaling corrected for
        serial dependence.
    standard_error : float or None
        Of ``annualised_sqrt_q``, from the general delta method form.
    ci_low, ci_high : float or None
        Two sided interval for ``annualised_sqrt_q`` at ``confidence_level``.
    confidence_level : float
    bandwidth : int
        Bartlett truncation lag actually used.
    lag_selection_parameter : int
        The ``n`` of Newey and West (1994) that produced the bandwidth.
    long_run_variance, sample_variance : float
    periods_per_year : float
    risk_free_rate_annual, risk_free_rate_per_period : float
    n_periods : int
    warnings : tuple of str

    Notes
    -----
    Two scalings are reported because their divergence is a diagnostic, not an
    error. A large gap says the per period returns are serially dependent, and
    the sign of the gap says in which direction: ``annualised_hac`` below
    ``annualised_sqrt_q`` means positive autocorrelation, which inflates the
    naive number.

    The interval is attached to the ``sqrt(q)`` scaling because that is the
    estimator whose asymptotic distribution the delta method of ``02`` section
    1.3 describes. The serial dependence still enters, through the long run
    covariance matrix used for ``Omega``. Attaching the same interval to
    ``annualised_hac`` would be wrong: that quantity is a ratio of two
    estimated objects and its variance carries an extra term this expression
    does not contain.

    **A Sharpe ratio without an interval does not enter the report.** When the
    interval cannot be formed, every field here is ``None`` and the reason sits
    in ``warnings``.
    """

    per_period_sample: float | None
    per_period_population: float | None
    annualised_sqrt_q: float | None
    annualised_hac: float | None
    standard_error: float | None
    ci_low: float | None
    ci_high: float | None
    confidence_level: float
    bandwidth: int
    lag_selection_parameter: int
    long_run_variance: float
    sample_variance: float
    periods_per_year: float
    risk_free_rate_annual: float
    risk_free_rate_per_period: float
    n_periods: int
    warnings: tuple[str, ...]

    @property
    def is_defined(self) -> bool:
        """True when the point estimate and its interval both exist."""
        return self.annualised_sqrt_q is not None and self.ci_low is not None


def sharpe_ratio(
    returns: PeriodReturns,
    *,
    risk_free_rate: float = 0.0,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    bandwidth: int | None = None,
) -> SharpeEstimate:
    """Sharpe ratio on a calendar grid, with a standard error that admits dependence.

    Parameters
    ----------
    returns : PeriodReturns
        Only this contract is accepted. ``TradeReturns`` has no
        ``periods_per_year`` and therefore cannot be passed. See D006.
    risk_free_rate : float, optional
        Simple **annual** rate, converted to the grid by
        :func:`de_annualise_rate`. Defaults to zero, and the value used is
        carried on the result, so the default is declared rather than silent.
    confidence_level : float, optional
        Two sided, defaults to :data:`DEFAULT_CONFIDENCE_LEVEL`.
    bandwidth : int or None, optional
        Bartlett truncation lag. ``None`` selects it by Newey and West (1994).
        An explicit value overrides the selection and is reported as used.

    Returns
    -------
    SharpeEstimate

    Raises
    ------
    InsufficientSampleError
        Fewer than two periods, where no dispersion can be formed at all. The
        declared minimum of ``MIN_PERIODS`` is a warning, not an error, per
        ``02`` section 1.4: the metric is still reported, and it is the report
        layer that suppresses the sections which depend on it.

    Notes
    -----
    Point estimate, per ``02`` section 1.2:

    ``SR_annual = sqrt(q) * mean(r - r_f) / sigma_LR``

    with ``sigma_LR`` the Newey and West (1987) long run standard deviation.

    **Declared divergence from Lo (2002).** Lo estimates the annualisation
    factor ``eta(q)`` from the finite sum of sample autocorrelations up to lag
    ``q - 1``. With ``q = 252`` and three years of daily data that is 251
    autocorrelations from 756 observations, and the estimator is unusable.
    Since ``Var(sum of q returns)`` converges to ``q * sigma_LR^2``, the
    identity ``eta(q) -> sqrt(q) * sigma / sigma_LR`` holds, so this
    implementation targets the same population object through a stable
    estimator. Measured recovery of ``eta(q)`` under AR(1) is in the notes of
    :func:`bartlett_long_run_variance`.

    Standard error, per ``02`` section 1.3. Delta method on the moment vector
    ``theta = (mu, s)`` with ``s = E[r^2]`` and ``SR = mu / sqrt(s - mu^2)``:

    ``dg/dmu = s / (s - mu^2)^(3/2)``

    ``dg/ds = -mu / (2 * (s - mu^2)^(3/2))``

    ``Var(SR) = (1/T) * grad' * Omega * grad``

    with ``Omega`` the long run covariance matrix of ``(r_t, r_t^2)``, estimated
    with the same kernel and the same bandwidth as ``sigma_LR``. One bandwidth
    is used for both objects rather than selecting separately, so that the two
    numbers in the report describe the same dependence structure; the
    alternative would let the point estimate and its interval disagree about
    how much memory the series has.

    Under independence this reduces exactly to
    :func:`mertens_sharpe_variance`.

    What it measures. The ratio of mean to dispersion of excess return per unit
    of calendar time, with an interval that reflects sampling error under
    stationarity and without assuming normality.

    What it does not measure. The probability that an edge survives a search
    over configurations, which is ``core/overfit.py``. Tail risk, since the
    variance is symmetric and penalises gain exactly like loss. Depth or
    duration of drawdown. It applies no correction for selection among
    multiple configurations.

    Invalid when. The sample is not stationary within itself: both the standard
    error and the scaling factor assume stationarity and ergodicity, and a
    structural break invalidates both.
    """
    values = np.asarray(returns.values, dtype=np.float64)
    n_obs = int(values.size)
    if n_obs < 2:
        raise InsufficientSampleError(
            "the Sharpe ratio needs at least two periods to form a dispersion",
            observed=n_obs,
            threshold=2,
        )

    per_period_rf = de_annualise_rate(risk_free_rate, returns.periods_per_year)
    excess = values - per_period_rf
    warnings: list[str] = []
    if n_obs < MIN_PERIODS:
        warnings.append(
            f"n_periods={n_obs} below MIN_PERIODS={MIN_PERIODS}: the HAC long run "
            "variance estimator is noise at this sample size, see 02 section 1.4"
        )

    lag_selection = newey_west_lag_selection(n_obs)
    chosen_bandwidth = newey_west_bandwidth(excess) if bandwidth is None else bandwidth
    long_run_variance = bartlett_long_run_variance(excess, chosen_bandwidth)
    sample_variance = float(excess.var(ddof=1))
    population_variance = float(excess.var(ddof=0))
    mean = float(excess.mean())
    quantile = float(norm.ppf(0.5 + confidence_level / 2.0))

    if dispersion_is_negligible(excess):
        warnings.append(
            "dispersion is not distinguishable from zero at double precision, so the "
            "Sharpe ratio is undefined rather than infinite, see 02 section 1.6"
        )
        return SharpeEstimate(
            per_period_sample=None,
            per_period_population=None,
            annualised_sqrt_q=None,
            annualised_hac=None,
            standard_error=None,
            ci_low=None,
            ci_high=None,
            confidence_level=confidence_level,
            bandwidth=chosen_bandwidth,
            lag_selection_parameter=lag_selection,
            long_run_variance=long_run_variance,
            sample_variance=sample_variance,
            periods_per_year=returns.periods_per_year,
            risk_free_rate_annual=risk_free_rate,
            risk_free_rate_per_period=per_period_rf,
            n_periods=n_obs,
            warnings=tuple(warnings),
        )

    root_q = math.sqrt(returns.periods_per_year)
    per_period_sample = mean / math.sqrt(sample_variance)
    per_period_population = mean / math.sqrt(population_variance)
    annualised_sqrt_q = root_q * per_period_sample
    annualised_hac = (
        root_q * mean / math.sqrt(long_run_variance) if long_run_variance > 0.0 else None
    )
    if annualised_hac is None:  # pragma: no cover
        # Unreachable with real data: the Bartlett weights make the long run
        # variance positive semidefinite, and an exact zero requires the
        # autocovariances to cancel to the last bit. A search over alternating
        # and ramp series found no (T, L) pair that reaches it. The guard stays
        # because floating point cancellation is the one way it could happen.
        warnings.append(
            "long run variance is zero at the selected bandwidth, so the HAC "
            "scaling is undefined; only the sqrt(q) scaling is reported"
        )

    second_moment = float((excess**2).mean())
    denominator = population_variance**1.5
    gradient = np.array([second_moment / denominator, -mean / (2.0 * denominator)])
    omega = bartlett_long_run_covariance(np.column_stack([excess, excess**2]), chosen_bandwidth)
    variance_of_sharpe = quadratic_form(gradient, omega) / n_obs
    if variance_of_sharpe <= 0.0:  # pragma: no cover
        # Same reason: Omega is positive semidefinite by construction, so this
        # quadratic form is non negative except under floating point
        # cancellation with a near null gradient.
        warnings.append(
            f"delta method variance is non positive ({variance_of_sharpe:.3e}) at "
            f"bandwidth {chosen_bandwidth}; no interval is reported"
        )
        standard_error = None
        ci_low = ci_high = None
    else:
        standard_error = root_q * math.sqrt(variance_of_sharpe)
        ci_low = annualised_sqrt_q - quantile * standard_error
        ci_high = annualised_sqrt_q + quantile * standard_error

    return SharpeEstimate(
        per_period_sample=per_period_sample,
        per_period_population=per_period_population,
        annualised_sqrt_q=annualised_sqrt_q,
        annualised_hac=annualised_hac,
        standard_error=standard_error,
        ci_low=ci_low,
        ci_high=ci_high,
        confidence_level=confidence_level,
        bandwidth=chosen_bandwidth,
        lag_selection_parameter=lag_selection,
        long_run_variance=long_run_variance,
        sample_variance=sample_variance,
        periods_per_year=returns.periods_per_year,
        risk_free_rate_annual=risk_free_rate,
        risk_free_rate_per_period=per_period_rf,
        n_periods=n_obs,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    """Statistics native to trade order. None of them carries a time unit.

    Attributes
    ----------
    n_trades : int
    expectancy : float
        Mean return per trade, on the basis of the series.
    hit_rate : float
        Fraction of trades with strictly positive return. Scratch trades count
        as neither win nor loss, and are visible as
        ``n_trades - n_wins - n_losses``.
    n_wins, n_losses : int
    profit_factor : float or None
        Gross gain over gross loss. ``None`` when there is no losing trade,
        because the ratio is undefined rather than infinite, exactly as for a
        Sharpe ratio of zero dispersion.
    win_loss_ratio : float or None
        Mean win over the magnitude of the mean loss.
    mean_win, mean_loss : float or None
    largest_win, largest_loss : float
    skewness, kurtosis : float
        Of the per trade return distribution, population moments. Kurtosis, not
        excess kurtosis.
    warnings : tuple of str

    Notes
    -----
    This class deliberately has no ``periods_per_year``, no ``period``, no
    ``calendar_id``, no ``years`` and no field whose name contains
    ``annual``. A test enumerates that prohibition and fails with an explicit
    message if anyone adds one, so D006 does not depend on review discipline.
    """

    n_trades: int
    expectancy: float
    hit_rate: float
    n_wins: int
    n_losses: int
    profit_factor: float | None
    win_loss_ratio: float | None
    mean_win: float | None
    mean_loss: float | None
    largest_win: float
    largest_loss: float
    skewness: float
    kurtosis: float
    warnings: tuple[str, ...]


def trade_metrics(returns: TradeReturns) -> TradeMetrics:
    """Statistics of the per trade return distribution.

    Parameters
    ----------
    returns : TradeReturns

    Returns
    -------
    TradeMetrics

    Raises
    ------
    InsufficientSampleError
        Empty series. Fewer than ``MIN_TRADES`` trades is a warning, not an
        error: ``02`` section 1.4 says to report the metrics and suppress the
        overfitting and regime sections, which is a decision for the report
        layer, not for this function.

    Notes
    -----
    Nothing computed here may be annualised, in any code path, ever. The index
    is trade number: its spacing is irregular and the arrival rate of signals
    is a sample realisation, not a parameter of the strategy. See D006 and
    ``02`` section 1.1.
    """
    values = np.asarray(returns.values, dtype=np.float64)
    n_obs = int(values.size)
    if n_obs == 0:
        raise InsufficientSampleError(
            "trade metrics need at least one trade", observed=0, threshold=1
        )
    warnings: list[str] = []
    if n_obs < MIN_TRADES:
        warnings.append(
            f"n_trades={n_obs} below MIN_TRADES={MIN_TRADES}: the third and fourth "
            "sample moments are too noisy to correct anything, see 02 section 1.4"
        )

    wins = values[values > 0.0]
    losses = values[values < 0.0]
    gross_gain = float(wins.sum())
    gross_loss = float(-losses.sum())
    mean = float(values.mean())
    dispersion = float(values.std(ddof=0))
    if not dispersion_is_negligible(values):
        centred = values - mean
        skewness = float((centred**3).mean()) / dispersion**3
        kurtosis = float((centred**4).mean()) / dispersion**4
    else:
        skewness = 0.0
        kurtosis = 0.0
        warnings.append("per trade dispersion is zero, so shape moments are reported as zero")

    return TradeMetrics(
        n_trades=n_obs,
        expectancy=mean,
        hit_rate=float(wins.size) / n_obs,
        n_wins=int(wins.size),
        n_losses=int(losses.size),
        profit_factor=gross_gain / gross_loss if gross_loss > 0.0 else None,
        win_loss_ratio=(
            float(wins.mean()) / float(-losses.mean()) if wins.size and losses.size else None
        ),
        mean_win=float(wins.mean()) if wins.size else None,
        mean_loss=float(losses.mean()) if losses.size else None,
        largest_win=float(values.max()),
        largest_loss=float(values.min()),
        skewness=skewness,
        kurtosis=kurtosis,
        warnings=tuple(warnings),
    )


def equity_curve(returns: PeriodReturns) -> FloatArray:
    """Equity path implied by the basis of the series.

    Parameters
    ----------
    returns : PeriodReturns

    Returns
    -------
    numpy.ndarray of float64
        Length ``n_periods + 1``, starting at ``initial_capital``.

    Notes
    -----
    Under ``FIXED_INITIAL`` the returns are additive, so equity is
    ``capital * (1 + cumsum(r))``. Under ``CURRENT_EQUITY`` they compose
    multiplicatively, so equity is ``capital * cumprod(1 + r)``. Building the
    path with the wrong rule silently changes the drawdown, which is why the
    basis is a mandatory contract field rather than an argument here.
    """
    values = np.asarray(returns.values, dtype=np.float64)
    path = np.empty(values.size + 1, dtype=np.float64)
    path[0] = returns.initial_capital
    if returns.basis is Basis.FIXED_INITIAL:
        path[1:] = returns.initial_capital * (1.0 + np.cumsum(values))
    else:
        path[1:] = returns.initial_capital * np.cumprod(1.0 + values)
    return path


@dataclass(frozen=True, slots=True)
class DrawdownProfile:
    """Depth and duration of the worst peak to trough excursion.

    Attributes
    ----------
    max_drawdown : float
        Positive fraction of the running peak lost at the worst point.
    max_drawdown_duration : int
        Number of periods from the peak that preceded the worst trough to the
        first period at or above that peak again. Still running at the end of
        the sample counts to the end, and ``recovered`` says which it was.
    recovered : bool
    time_underwater : float
        Fraction of periods spent strictly below the running peak.

    Notes
    -----
    The observed maximum drawdown is one realisation of a random variable and
    is almost always optimistic. Its distribution comes from ``core/risk.py``
    in v0.3, and the number reported here is placed as a quantile of that
    distribution rather than read on its own.
    """

    max_drawdown: float
    max_drawdown_duration: int
    recovered: bool
    time_underwater: float


def drawdown_profile(equity: FloatArray) -> DrawdownProfile:
    """Drawdown depth, duration and time underwater from an equity path.

    Parameters
    ----------
    equity : numpy.ndarray of float64
        Strictly positive path, as produced by :func:`equity_curve`.

    Returns
    -------
    DrawdownProfile

    Raises
    ------
    InsufficientSampleError
        Path shorter than two points, or reaching zero, where the relative
        drawdown is undefined.
    """
    if equity.size < 2:
        raise InsufficientSampleError(
            "drawdown needs at least two equity points", observed=int(equity.size), threshold=2
        )
    if bool((equity <= 0.0).any()):
        raise InsufficientSampleError(
            "relative drawdown is undefined on a path that reaches zero; "
            "the account was ruined and the ratio has no meaning below that point",
            observed=float(equity.min()),
            threshold=0.0,
        )
    peak = np.maximum.accumulate(equity)
    underwater = equity / peak - 1.0
    trough = int(np.argmin(underwater))
    max_drawdown = float(-underwater[trough])
    peak_index = int(np.argmax(equity[: trough + 1]))
    after = np.flatnonzero(equity[trough:] >= equity[peak_index])
    recovered = bool(after.size)
    end_index = trough + int(after[0]) if recovered else equity.size - 1
    return DrawdownProfile(
        max_drawdown=max_drawdown,
        max_drawdown_duration=end_index - peak_index,
        recovered=recovered,
        time_underwater=float((underwater[1:] < 0.0).mean()),
    )


@dataclass(frozen=True, slots=True)
class PeriodMetrics:
    """Calendar anchored statistics. The only place annualised numbers exist.

    Attributes
    ----------
    period : Period
    periods_per_year : float
    calendar_id : str
    basis : Basis
    initial_capital : float
    n_periods : int
    active_fraction : float
    years : float
    cumulative_return : float
    cagr : float or None
        ``None`` when terminal equity is non positive, where the root of a non
        positive number is not a growth rate.
    volatility_annualised : float
    sharpe : SharpeEstimate
    sortino_annualised : float or None
    kelly_fraction : float or None
    drawdown : DrawdownProfile or None
    warnings : tuple of str

    Notes
    -----
    Every field that changes the number is present, because ``01`` requires the
    report to be reproducible from it: ``period``, ``periods_per_year``,
    ``calendar_id``, ``basis``, ``initial_capital``, ``active_fraction``, and
    through :class:`SharpeEstimate` also the risk free rate, the HAC bandwidth
    and the confidence level.
    """

    period: Period
    periods_per_year: float
    calendar_id: str
    basis: Basis
    initial_capital: float
    n_periods: int
    active_fraction: float
    years: float
    cumulative_return: float
    cagr: float | None
    volatility_annualised: float
    sharpe: SharpeEstimate
    sortino_annualised: float | None
    kelly_fraction: float | None
    drawdown: DrawdownProfile | None
    warnings: tuple[str, ...]


def period_metrics(
    returns: PeriodReturns,
    *,
    risk_free_rate: float = 0.0,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    bandwidth: int | None = None,
) -> PeriodMetrics:
    """Calendar anchored descriptive statistics of a return series.

    Parameters
    ----------
    returns : PeriodReturns
    risk_free_rate : float, optional
        Simple annual rate. See :func:`de_annualise_rate`.
    confidence_level : float, optional
    bandwidth : int or None, optional

    Returns
    -------
    PeriodMetrics

    Notes
    -----
    Sortino uses a minimum acceptable return equal to the risk free rate, and
    the downside deviation divides by the full sample size rather than by the
    count of losing periods, following Sortino and Price (1994). Dividing by
    the count of losing periods produces a number that rises when losses become
    rarer but larger, which inverts the ranking the measure exists to provide.

    The Kelly fraction is ``mu / sigma^2`` per period. It is invariant to the
    grid step up to estimation error, because mean and variance both scale
    linearly in the period length, which makes it a useful cross check that the
    grid projection is not distorting the moments.
    """
    values = np.asarray(returns.values, dtype=np.float64)
    sharpe = sharpe_ratio(
        returns,
        risk_free_rate=risk_free_rate,
        confidence_level=confidence_level,
        bandwidth=bandwidth,
    )
    per_period_rf = sharpe.risk_free_rate_per_period
    excess = values - per_period_rf
    root_q = math.sqrt(returns.periods_per_year)

    warnings: list[str] = list(sharpe.warnings)
    if returns.active_fraction < 1.0:
        warnings.append(
            f"active_fraction={returns.active_fraction:.4f}: every statistic here is "
            "diluted by the empty periods, which is the correct treatment because "
            "parked capital is allocated capital, see 02 section 1.6"
        )

    path = equity_curve(returns)
    cumulative_return = float(path[-1] / returns.initial_capital - 1.0)
    cagr = (
        float((path[-1] / returns.initial_capital) ** (1.0 / returns.years) - 1.0)
        if path[-1] > 0.0
        else None
    )
    if cagr is None:
        warnings.append("terminal equity is non positive, so the compound growth rate is undefined")

    downside = np.minimum(excess, 0.0)
    downside_deviation = float(np.sqrt((downside**2).mean()))
    sortino = (
        root_q * float(excess.mean()) / downside_deviation if downside_deviation > 0.0 else None
    )
    if sortino is None:
        warnings.append(
            "no period fell below the minimum acceptable return, so Sortino is undefined"
        )

    population_variance = float(excess.var(ddof=0))
    kelly = float(excess.mean()) / population_variance if population_variance > 0.0 else None

    try:
        profile: DrawdownProfile | None = drawdown_profile(path)
    except InsufficientSampleError as exc:
        profile = None
        warnings.append(f"drawdown not computed: {exc}")

    return PeriodMetrics(
        period=returns.period,
        periods_per_year=returns.periods_per_year,
        calendar_id=returns.calendar_id,
        basis=returns.basis,
        initial_capital=returns.initial_capital,
        n_periods=returns.n_periods,
        active_fraction=returns.active_fraction,
        years=returns.years,
        cumulative_return=cumulative_return,
        cagr=cagr,
        volatility_annualised=root_q * float(values.std(ddof=1)),
        sharpe=sharpe,
        sortino_annualised=sortino,
        kelly_fraction=kelly,
        drawdown=profile,
        warnings=tuple(warnings),
    )
