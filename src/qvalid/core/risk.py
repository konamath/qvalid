"""Risk measures over simulated equity paths.

Everything here reads :class:`~qvalid.contracts.EquityPaths` and returns an
estimate with its own Monte Carlo standard error, because ``02`` requires a
quantile never to be reported without the uncertainty of the quantile.

The organising point of the module is that the observed backtest supplies one
number and the simulation supplies a distribution. A maximum drawdown of twelve
per cent read off a backtest is one draw from a random variable whose mean may
be twenty; :func:`drawdown_distribution` places the observed value as a quantile
of the simulated distribution, which is the only reading of it that carries
information.

Expected shortfall is the principal measure and value at risk is secondary.
Expected shortfall is coherent in the sense of Artzner, Delbaen, Eber and Heath
(1999), in particular subadditive, so it cannot reward splitting a position into
two. Value at risk is reported because it is the number people quote.

Discrete monitoring, stated before it surprises anyone
-------------------------------------------------------
A barrier checked once per period is crossed less often than a continuously
monitored one, because a path can dip below it and return between two
observations. Every barrier statistic here therefore understates the continuous
time answer by a known amount. The correction of Broadie, Glasserman and Kou
(1997), a shift of ``0.5826 * sigma * sqrt(dt)``, is exposed in
:func:`brownian_ruin_probability` so the two can be compared rather than
confused. Measured, the uncorrected form is off by 10 to 43 Monte Carlo standard
errors on samples this module handles routinely. See D022.

References
----------
Artzner, P., Delbaen, F., Eber, J.-M., and Heath, D. (1999). Coherent measures
of risk. Mathematical Finance 9(3), 203-228.

Broadie, M., Glasserman, P., and Kou, S. (1997). A continuity correction for
discrete barrier options. Mathematical Finance 7(4), 325-349.

Magdon-Ismail, M., Atiya, A. F., Pratap, A., and Abu-Mostafa, Y. S. (2004). On
the maximum drawdown of a Brownian motion. Journal of Applied Probability 41(1),
147-161.

Kelly, J. L. (1956). A new interpretation of information rate. Bell System
Technical Journal 35(4), 917-926.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from qvalid.contracts import Basis, EquityPaths, FloatArray, IntArray, Unit
from qvalid.core.constants import (
    BARRIER_CONTINUITY_CORRECTION,
    DEFAULT_CONFIDENCE_LEVEL,
    EXPECTED_MAX_DRAWDOWN_COEFFICIENT,
)
from qvalid.exceptions import InsufficientSampleError, UnitMismatchError

__all__ = [
    "DEFAULT_TAIL_LEVEL",
    "DrawdownDistribution",
    "FirstPassage",
    "KellyEstimate",
    "MonteCarloEstimate",
    "absorb_at_barrier",
    "brownian_ruin_probability",
    "drawdown_distribution",
    "expected_max_drawdown",
    "expected_shortfall",
    "first_passage",
    "kelly_from_paths",
    "max_drawdown_per_path",
    "path_returns",
    "terminal_return",
    "value_at_risk",
]

DEFAULT_TAIL_LEVEL = 0.95
"""Tail level of the reported value at risk and expected shortfall."""

_MIN_PATHS_FOR_TAIL = 20
"""Below this, an empirical tail quantile is a single order statistic, not an estimate."""


@dataclass(frozen=True, slots=True)
class MonteCarloEstimate:
    """A simulated quantity with the uncertainty of the simulation attached.

    Attributes
    ----------
    value : float
    standard_error : float
        Of the estimate itself, across replications. Not the dispersion of the
        underlying distribution.
    ci_low, ci_high : float
        Percentile interval from a bootstrap over the paths.
    confidence_level : float
    n_paths : int
    seed : int
        Of the bootstrap used for the interval, so the interval is reproducible.

    Notes
    -----
    ``02`` forbids reporting a quantile without the uncertainty of the quantile.
    This type is the mechanism: there is no way to return a bare float from the
    public surface of this module.
    """

    value: float
    standard_error: float
    ci_low: float
    ci_high: float
    confidence_level: float
    n_paths: int
    seed: int


def _require_paths(paths: EquityPaths, minimum: int = 1) -> FloatArray:
    values = np.asarray(paths.values, dtype=np.float64)
    if paths.n_paths < minimum:
        raise InsufficientSampleError(
            "too few simulated paths for this estimate",
            observed=paths.n_paths,
            threshold=minimum,
        )
    if paths.n_steps < 2:
        raise InsufficientSampleError(
            "paths must hold at least one step beyond the starting level",
            observed=paths.n_steps,
            threshold=2,
        )
    return values


def _require_period_unit(paths: EquityPaths, what: str) -> None:
    """Refuse a trade indexed path where a calendar horizon is required.

    ``02`` section 5 is explicit: a horizon measured in trades is not a horizon.
    The arrival rate of trades is a sample realisation, so "probability of ruin
    within 250 trades" answers a question nobody asked, and would be read as if
    it said "within a year".
    """
    if paths.unit is not Unit.PERIOD:
        raise UnitMismatchError(
            f"{what} requires EquityPaths with unit=PERIOD, got unit={paths.unit}; "
            "a horizon measured in trades is not a horizon, because the arrival rate "
            "of trades is a sample realisation and not a parameter of the strategy"
        )


def path_returns(paths: EquityPaths, basis: Basis) -> FloatArray:
    """Per step returns implied by a path of equity levels.

    Parameters
    ----------
    paths : EquityPaths
    basis : Basis
        Mandatory, because it is **not** recoverable from the levels. Under
        ``FIXED_INITIAL`` the step return is the change over the initial
        capital; under ``CURRENT_EQUITY`` it is the change over the previous
        level. Given only the levels the two are indistinguishable, so a
        function that guessed would silently produce the wrong second moment.
        This corrects a claim made in D020.

    Returns
    -------
    numpy.ndarray of float64, shape ``(n_paths, n_steps - 1)``
    """
    values = _require_paths(paths)
    difference = np.diff(values, axis=1)
    if basis is Basis.FIXED_INITIAL:
        return np.ascontiguousarray(difference / values[:, :1])
    return np.ascontiguousarray(difference / values[:, :-1])


def terminal_return(paths: EquityPaths) -> FloatArray:
    """Total return of each path over its whole horizon.

    Returns
    -------
    numpy.ndarray of float64, shape ``(n_paths,)``
        ``final / initial - 1``. Independent of basis, since it is a ratio of
        levels.
    """
    values = _require_paths(paths)
    return np.ascontiguousarray(values[:, -1] / values[:, 0] - 1.0)


def max_drawdown_per_path(paths: EquityPaths) -> FloatArray:
    """Deepest peak to trough loss of each path, as a positive fraction.

    Returns
    -------
    numpy.ndarray of float64, shape ``(n_paths,)``

    Raises
    ------
    InsufficientSampleError
        If any path reaches zero or below, where the relative drawdown has no
        meaning. Under ``CURRENT_EQUITY`` that is ruin, and
        :func:`first_passage` is the function that measures it.
    """
    values = _require_paths(paths)
    if bool((values <= 0.0).any()):
        raise InsufficientSampleError(
            "relative drawdown is undefined on a path that reaches zero; the account "
            "was ruined and the ratio has no meaning below that point. Use first_passage "
            "to measure ruin, or resample under basis=FIXED_INITIAL",
            observed=float(values.min()),
            threshold=0.0,
        )
    running_peak = np.maximum.accumulate(values, axis=1)
    return np.ascontiguousarray((1.0 - values / running_peak).max(axis=1))


def _bootstrap_interval(
    sample: FloatArray,
    statistic: str,
    *,
    level: float,
    confidence_level: float,
    seed: int,
    n_replications: int,
) -> MonteCarloEstimate:
    """Percentile interval for a tail statistic, by i.i.d. bootstrap over paths.

    The bootstrap here is i.i.d. and not blockwise, and that is not an
    oversight: the paths are independent of one another by construction, so
    there is no serial dependence across the resampling dimension to preserve.
    Dependence within a path was already handled by the block scheme that
    generated it.
    """
    n_paths = int(sample.size)
    rng = np.random.default_rng(seed)
    index = rng.integers(0, n_paths, size=(n_replications, n_paths))
    replicates = sample[index]
    if statistic == "var":
        values = -np.percentile(replicates, 100.0 * (1.0 - level), axis=1)
        point = -float(np.percentile(sample, 100.0 * (1.0 - level)))
    else:
        cutoff = np.percentile(replicates, 100.0 * (1.0 - level), axis=1, keepdims=True)
        masked = np.where(replicates <= cutoff, replicates, np.nan)
        values = -np.nanmean(masked, axis=1)
        sample_cutoff = float(np.percentile(sample, 100.0 * (1.0 - level)))
        point = -float(sample[sample <= sample_cutoff].mean())
    tail = (1.0 - confidence_level) / 2.0
    low, high = np.percentile(values, [100.0 * tail, 100.0 * (1.0 - tail)])
    return MonteCarloEstimate(
        value=point,
        standard_error=float(values.std(ddof=1)),
        ci_low=float(low),
        ci_high=float(high),
        confidence_level=confidence_level,
        n_paths=n_paths,
        seed=seed,
    )


def value_at_risk(
    paths: EquityPaths,
    *,
    level: float = DEFAULT_TAIL_LEVEL,
    seed: int,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_replications: int = 1_000,
) -> MonteCarloEstimate:
    """Empirical value at risk of the terminal return, as a positive loss.

    Parameters
    ----------
    paths : EquityPaths
    level : float, optional
        Tail level. ``0.95`` reports the loss exceeded by 5 per cent of paths.
    seed : int
        Mandatory. Seeds the bootstrap that produces the interval.
    confidence_level : float, optional
    n_replications : int, optional
        Bootstrap replications for the interval. Explicit, and reported.

    Returns
    -------
    MonteCarloEstimate
        ``value`` is a positive number for a loss and negative if even the tail
        of the distribution is a gain.

    Notes
    -----
    What it measures. The threshold that terminal loss exceeds with probability
    ``1 - level``, under the resampling scheme that generated the paths.

    What it does not measure. Anything about how bad the loss is once the
    threshold is breached, which is exactly what :func:`expected_shortfall`
    adds. Value at risk is also not subadditive, so it can reward splitting a
    position in two, and it is reported here only because it is the number
    people quote. Expected shortfall is the coherent measure in the sense of
    Artzner, Delbaen, Eber and Heath (1999) and is the one to read.

    Invalid when. The paths were generated from a sample that never visited the
    regime in question. Resampling cannot manufacture a state the history does
    not contain, so the tail is bounded below by the worst realised sequence.
    """
    sample = terminal_return(paths)
    if sample.size < _MIN_PATHS_FOR_TAIL:
        raise InsufficientSampleError(
            "an empirical tail quantile from this few paths is a single order "
            "statistic, not an estimate",
            observed=int(sample.size),
            threshold=_MIN_PATHS_FOR_TAIL,
        )
    _check_level(level)
    return _bootstrap_interval(
        sample,
        "var",
        level=level,
        confidence_level=confidence_level,
        seed=seed,
        n_replications=n_replications,
    )


def expected_shortfall(
    paths: EquityPaths,
    *,
    level: float = DEFAULT_TAIL_LEVEL,
    seed: int,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_replications: int = 1_000,
) -> MonteCarloEstimate:
    """Mean terminal loss conditional on being in the tail, as a positive loss.

    Parameters
    ----------
    Same as :func:`value_at_risk`.

    Returns
    -------
    MonteCarloEstimate

    Notes
    -----
    Coherent in the sense of Artzner, Delbaen, Eber and Heath (1999):
    monotone, subadditive, positively homogeneous and translation invariant.
    Subadditivity is the property value at risk lacks and the reason this is
    the principal measure of the module.

    It is at least as large as the value at risk at the same level, by
    construction, since it averages over the region beyond that threshold. A
    test asserts the inequality on every generated case rather than trusting
    the argument.
    """
    sample = terminal_return(paths)
    if sample.size < _MIN_PATHS_FOR_TAIL:
        raise InsufficientSampleError(
            "an empirical tail mean from this few paths averages a handful of order "
            "statistics and has no coverage claim",
            observed=int(sample.size),
            threshold=_MIN_PATHS_FOR_TAIL,
        )
    _check_level(level)
    return _bootstrap_interval(
        sample,
        "es",
        level=level,
        confidence_level=confidence_level,
        seed=seed,
        n_replications=n_replications,
    )


def _check_level(level: float) -> None:
    if not 0.0 < level < 1.0:
        raise InsufficientSampleError(
            "tail level must lie in the open interval (0, 1)", observed=level, threshold=(0.0, 1.0)
        )


@dataclass(frozen=True, slots=True)
class DrawdownDistribution:
    """Distribution of the maximum drawdown, with the observed value located in it.

    Attributes
    ----------
    mean : MonteCarloEstimate
    quantiles : dict of float to float
        Requested quantiles of the simulated maximum drawdown.
    observed : float or None
        The maximum drawdown of the realised backtest, if supplied.
    observed_quantile : float or None
        Fraction of simulated paths whose maximum drawdown is at or below the
        observed one. A value of 0.15 says the backtest was luckier than 85 per
        cent of the histories its own process can generate.
    n_paths : int

    Notes
    -----
    The point of the type is the last field. The observed maximum drawdown is
    one realisation of a random variable and is almost always optimistic,
    because a strategy is kept and reported precisely when its realised path was
    benign. Reading it without the distribution around it is the error this
    module exists to prevent.
    """

    mean: MonteCarloEstimate
    quantiles: dict[float, float]
    observed: float | None
    observed_quantile: float | None
    n_paths: int


def drawdown_distribution(
    paths: EquityPaths,
    *,
    seed: int,
    observed: float | None = None,
    quantiles: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95),
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_replications: int = 1_000,
) -> DrawdownDistribution:
    """Distribution of the maximum drawdown across simulated paths.

    Parameters
    ----------
    paths : EquityPaths
    seed : int
        Mandatory, seeds the bootstrap for the interval on the mean.
    observed : float or None, optional
        Maximum drawdown of the realised backtest, as a positive fraction,
        typically from :func:`~qvalid.core.metrics.drawdown_profile`.
    quantiles : tuple of float, optional
    confidence_level, n_replications : optional

    Returns
    -------
    DrawdownDistribution
    """
    sample = max_drawdown_per_path(paths)
    rng = np.random.default_rng(seed)
    index = rng.integers(0, sample.size, size=(n_replications, sample.size))
    means = sample[index].mean(axis=1)
    tail = (1.0 - confidence_level) / 2.0
    low, high = np.percentile(means, [100.0 * tail, 100.0 * (1.0 - tail)])
    mean_estimate = MonteCarloEstimate(
        value=float(sample.mean()),
        standard_error=float(means.std(ddof=1)),
        ci_low=float(low),
        ci_high=float(high),
        confidence_level=confidence_level,
        n_paths=int(sample.size),
        seed=seed,
    )
    return DrawdownDistribution(
        mean=mean_estimate,
        quantiles={q: float(np.quantile(sample, q)) for q in quantiles},
        observed=observed,
        observed_quantile=None if observed is None else float((sample <= observed).mean()),
        n_paths=int(sample.size),
    )


@dataclass(frozen=True, slots=True)
class FirstPassage:
    """Probability of hitting an absorbing barrier, and when.

    Attributes
    ----------
    probability : MonteCarloEstimate
        Of hitting the barrier at any monitored step within the horizon.
    barrier : float
        Absolute equity level, in account currency.
    horizon_steps : int
    steps_to_barrier : numpy.ndarray of int64
        Step index of first passage per path, ``-1`` for paths that never hit.
    quantiles : dict of float to float
        Of the first passage time, over the paths that did hit. Empty when none
        hit, because a quantile of an empty set is not zero.
    never_hit_fraction : float

    Notes
    -----
    Conditioning matters and is stated: the time quantiles are conditional on
    hitting. Reporting an unconditional median of a variable that is undefined
    for most paths would produce a number that looks reassuring for the wrong
    reason.
    """

    probability: MonteCarloEstimate
    barrier: float
    horizon_steps: int
    steps_to_barrier: IntArray
    quantiles: dict[float, float]
    never_hit_fraction: float


def first_passage(
    paths: EquityPaths,
    *,
    barrier: float,
    seed: int,
    quantiles: tuple[float, ...] = (0.05, 0.5, 0.95),
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_replications: int = 1_000,
) -> FirstPassage:
    """Probability of ruin over the declared horizon, and the time to the barrier.

    Parameters
    ----------
    paths : EquityPaths
        Must carry ``unit`` equal to ``PERIOD``.
    barrier : float
        Minimum operating capital, as an absolute level. No default: the
        barrier determines the answer, and a default would be a silent
        statistical parameter, which ``04`` forbids.
    seed : int
    quantiles : tuple of float, optional
        Of the first passage time, conditional on hitting.
    confidence_level, n_replications : optional

    Returns
    -------
    FirstPassage

    Raises
    ------
    UnitMismatchError
        If the paths are indexed by trade. See ``02`` section 5.
    InsufficientSampleError
        If the barrier is at or above the starting equity, where every path is
        ruined at step zero and the statistic is vacuous.

    Notes
    -----
    Hypotheses. The resampling scheme that produced the paths is a valid model
    of the process, which requires weak stationarity and short range dependence.
    Under a structural break the tail is understated and so is this probability.

    Discrete monitoring. The barrier is checked once per period, so a path that
    dips below it and returns within a period is not counted. The resulting
    probability is therefore below the continuously monitored one by a known
    amount; :func:`brownian_ruin_probability` exposes the correction of
    Broadie, Glasserman and Kou (1997) for comparison. This is not a defect of
    the estimator, it is a faithful model of an account that is marked once a
    day.
    """
    values = _require_paths(paths)
    _require_period_unit(paths, "risk of ruin over a declared horizon")
    starting = float(values[0, 0])
    if barrier >= starting:
        raise InsufficientSampleError(
            "the barrier is at or above the starting equity, so every path is ruined "
            "before the first step and the statistic carries no information",
            observed=barrier,
            threshold=starting,
        )

    breached = values <= barrier
    hit = breached.any(axis=1)
    steps = np.where(hit, breached.argmax(axis=1), -1).astype(np.int64)

    rng = np.random.default_rng(seed)
    index = rng.integers(0, hit.size, size=(n_replications, hit.size))
    replicated = hit[index].mean(axis=1)
    tail = (1.0 - confidence_level) / 2.0
    low, high = np.percentile(replicated, [100.0 * tail, 100.0 * (1.0 - tail)])
    probability = MonteCarloEstimate(
        value=float(hit.mean()),
        standard_error=float(replicated.std(ddof=1)),
        ci_low=float(low),
        ci_high=float(high),
        confidence_level=confidence_level,
        n_paths=int(hit.size),
        seed=seed,
    )
    hit_steps = steps[steps >= 0]
    return FirstPassage(
        probability=probability,
        barrier=barrier,
        horizon_steps=paths.n_steps - 1,
        steps_to_barrier=np.ascontiguousarray(steps),
        quantiles=(
            {q: float(np.quantile(hit_steps, q)) for q in quantiles} if hit_steps.size else {}
        ),
        never_hit_fraction=float(1.0 - hit.mean()),
    )


def absorb_at_barrier(paths: EquityPaths, barrier: float) -> EquityPaths:
    """Freeze every path at the barrier from its first passage onward.

    Parameters
    ----------
    paths : EquityPaths
    barrier : float

    Returns
    -------
    EquityPaths
        A new contract with the same seed and a method identifier marked as
        absorbed, so the report can tell the two apart.

    Notes
    -----
    Absorption is never applied silently, and it is **not** uniformly
    conservative. It lowers the terminal value of every path that breached the
    barrier and recovered, which is the effect people expect. It also raises the
    terminal value of every path that ended below the barrier, because such an
    account would have been closed at the barrier rather than continuing down.
    The deep tail therefore looks better after absorption, so an absorbed
    expected shortfall must not be read as the conservative figure. A test pins
    the direction rather than leaving it to intuition.

    Two further limits. The model assumes the stop out executes exactly at the
    barrier, with no gap and no slippage, which flatters it again. And applying
    absorption by default would change value at risk and expected shortfall
    without the caller asking, which ``04`` forbids for any parameter that
    changes the result. The caller chooses, and the choice is visible in
    ``method``.
    """
    values = _require_paths(paths)
    breached = np.maximum.accumulate(values <= barrier, axis=1)
    absorbed = np.where(breached, barrier, values)
    return EquityPaths(
        values=np.ascontiguousarray(absorbed, dtype=np.float64),
        unit=paths.unit,
        seed=paths.seed,
        method=f"{paths.method}+absorbed_at_{barrier:g}",
        period=paths.period,
    )


@dataclass(frozen=True, slots=True)
class KellyEstimate:
    """Kelly fraction with the uncertainty the bootstrap already produced.

    Attributes
    ----------
    point : float
        ``mean / variance`` of the per step returns pooled across paths.
    adjusted : float
        Lower quantile of the per path Kelly fractions, at
        ``1 - confidence_level``. The number to size with, since the point
        estimate is a ratio of two noisy moments and overshoots on average.
    quantiles : dict of float to float
    confidence_level : float
    basis : Basis
    n_paths : int

    Notes
    -----
    Declared divergence. The classical uncertainty adjustment shrinks the Kelly
    fraction under a normal prior on the mean, which requires declaring a prior.
    The adjustment here is instead the lower quantile of the bootstrap
    distribution of the fraction, which uses the uncertainty already produced by
    the resampling scheme and introduces no prior. The two agree in spirit and
    not in number, and the difference is documented rather than hidden.

    The fraction is invariant to the grid step up to estimation error, because
    mean and variance both scale linearly in the period length. That invariance
    is asserted in the metrics test suite and is a useful check that the grid
    projection rescales the moments rather than distorting them.
    """

    point: float
    adjusted: float
    quantiles: dict[float, float]
    confidence_level: float
    basis: Basis
    n_paths: int


def kelly_from_paths(
    paths: EquityPaths,
    basis: Basis,
    *,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    quantiles: tuple[float, ...] = (0.05, 0.5, 0.95),
) -> KellyEstimate:
    """Kelly fraction and its bootstrap adjusted counterpart.

    Parameters
    ----------
    paths : EquityPaths
    basis : Basis
        Mandatory. See :func:`path_returns`.
    confidence_level : float, optional
        The adjusted fraction is the ``1 - confidence_level`` quantile.
    quantiles : tuple of float, optional

    Returns
    -------
    KellyEstimate

    Raises
    ------
    InsufficientSampleError
        If the pooled dispersion is zero, where the fraction is unbounded
        rather than large.

    Notes
    -----
    Measured recovery on Gaussian paths of 1000 steps with a declared per step
    mean and a standard deviation of 0.01, so that the true fraction is
    ``mu / sigma^2``: at a true 4.00 the point estimate is 3.94 and the adjusted
    one is -1.28; at a true 12.00 they are 11.94 and 6.73; at a true 0.00 they
    are -0.06 and -5.33.

    The adjusted number being negative under a real edge of 4 is not a defect.
    It says that a thousand observations do not rule out a negative fraction at
    95 per cent confidence, which is the correct instruction: do not lever. The
    per path fraction is a ratio of a noisy mean to a noisy variance and is
    heavy tailed, so its lower quantile is far below its median by construction.
    """
    returns = path_returns(paths, basis)
    pooled_variance = float(returns.var(ddof=0))
    if pooled_variance <= 0.0:
        raise InsufficientSampleError(
            "the Kelly fraction is unbounded when the return dispersion is zero, not merely large",
            observed=pooled_variance,
            threshold=0.0,
        )
    per_path_variance = returns.var(axis=1, ddof=0)
    usable = per_path_variance > 0.0
    per_path = np.full(returns.shape[0], np.nan)
    per_path[usable] = returns.mean(axis=1)[usable] / per_path_variance[usable]
    finite = per_path[np.isfinite(per_path)]
    return KellyEstimate(
        point=float(returns.mean()) / pooled_variance,
        adjusted=float(np.quantile(finite, 1.0 - confidence_level)),
        quantiles={q: float(np.quantile(finite, q)) for q in quantiles},
        confidence_level=confidence_level,
        basis=basis,
        n_paths=int(paths.n_paths),
    )


def brownian_ruin_probability(
    barrier_distance: float,
    step_sigma: float,
    n_steps: int,
    *,
    continuity_corrected: bool = True,
) -> float:
    """Evaluate the closed form probability of a driftless walk hitting one barrier.

    Parameters
    ----------
    barrier_distance : float
        Distance from the starting level down to the barrier, positive.
    step_sigma : float
        Standard deviation of one step.
    n_steps : int
    continuity_corrected : bool, optional
        ``True`` applies the shift of Broadie, Glasserman and Kou (1997) for a
        barrier monitored once per step. ``False`` gives the continuous time
        reflection principle.

    Returns
    -------
    float
        ``2 * Phi(-(a + beta * sigma) / (sigma * sqrt(n)))`` when corrected, and
        the same without ``beta`` when not.

    Notes
    -----
    Exposed as a library function rather than kept in the test suite because
    the difference between the two forms is a diagnostic worth showing next to
    a simulated probability. Measured over 300000 paths, the uncorrected form
    sits 10 to 43 Monte Carlo standard errors away from the simulation and the
    corrected form within 1.3. Reporting the uncorrected number next to a
    discretely monitored simulation and calling the gap a bug is a mistake this
    function exists to prevent. See D022.

    Valid only for a driftless walk with a single lower barrier and Gaussian
    steps. It is a reference point, not the estimator: the estimator is
    :func:`first_passage`, which makes no distributional assumption.
    """
    if barrier_distance <= 0.0 or step_sigma <= 0.0 or n_steps < 1:
        raise InsufficientSampleError(
            "barrier distance and step sigma must be positive and the horizon at least one step",
            observed=(barrier_distance, step_sigma, n_steps),
            threshold=(0.0, 0.0, 1),
        )
    shift = BARRIER_CONTINUITY_CORRECTION * step_sigma if continuity_corrected else 0.0
    return float(2.0 * norm.cdf(-(barrier_distance + shift) / (step_sigma * math.sqrt(n_steps))))


def expected_max_drawdown(
    step_sigma: float, n_steps: int, *, continuity_corrected: bool = True
) -> float:
    """Evaluate the closed form expected maximum drawdown of a driftless walk.

    Parameters
    ----------
    step_sigma : float
    n_steps : int
    continuity_corrected : bool, optional

    Returns
    -------
    float
        ``sqrt(pi/2) * sigma * sqrt(n)`` from Magdon-Ismail, Atiya, Pratap and
        Abu-Mostafa (2004), minus ``2 * beta * sigma`` when corrected.

    Notes
    -----
    The continuity correction enters **twice**, once for the running maximum and
    once for the current level, because a drawdown is the gap between two
    quantities each of which is monitored discretely. That doubling is not in
    the original paper; it was obtained by measurement here. Ratios of
    simulation to this form: 0.996 at ``n = 60``, 0.998 at 252, 1.0001 at 1000
    and 1.0003 at 4000. Against the uncorrected form the same ratios are 0.876,
    0.940, 0.971 and 0.986, so on a sixty period sample the uncorrected form is
    twelve per cent too high.

    Reference point only, and for a driftless walk. A strategy with an edge has
    drift, and drift changes the expected drawdown substantially; the estimator
    to use is :func:`drawdown_distribution`, which assumes nothing.
    """
    if step_sigma <= 0.0 or n_steps < 1:
        raise InsufficientSampleError(
            "step sigma must be positive and the horizon at least one step",
            observed=(step_sigma, n_steps),
            threshold=(0.0, 1),
        )
    continuous = EXPECTED_MAX_DRAWDOWN_COEFFICIENT * step_sigma * math.sqrt(n_steps)
    if not continuity_corrected:
        return float(continuous)
    return float(continuous - 2.0 * BARRIER_CONTINUITY_CORRECTION * step_sigma)
