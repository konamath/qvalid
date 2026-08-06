"""Corrections for the search that produced the strategy.

This is the section that separates the project from a spreadsheet of metrics.
Everything in ``core/metrics.py`` describes one realised series. Everything here
asks a different question: given that the series was *selected* out of a search,
how much of what it shows survives the selection.

Four instruments, in increasing order of what they require from the user:

:func:`probabilistic_sharpe_ratio`
    Needs only the series. Probability that the true Sharpe exceeds a threshold.

:func:`minimum_track_record_length`
    Needs only the series. How long a track record would have to be before the
    observed Sharpe is distinguishable from a threshold.

:func:`deflated_sharpe_ratio`
    Needs the number of configurations tried and the dispersion of their Sharpe
    ratios. Without them it does not run, per D004: estimating the number of
    trials would be fabricating the input that determines the result.

:func:`probability_of_backtest_overfitting` and
:func:`superior_predictive_ability`
    Need the whole :class:`~qvalid.contracts.TrialMatrix`, every configuration
    tested rather than the winner alone.

The precondition of ``02`` section 3, that all configurations share one grid and
one ``periods_per_year``, is structural rather than checked: a ``TrialMatrix``
declares the grid once for the whole matrix, so no representable state violates
it.

References
----------
Bailey, D. H., and López de Prado, M. (2014). The deflated Sharpe ratio:
correcting for selection bias, backtest overfitting and non-normality. Journal
of Portfolio Management 40(5), 94-107.

Bailey, D. H., Borwein, J., López de Prado, M., and Zhu, Q. J. (2017). The
probability of backtest overfitting. Journal of Computational Finance 20(4),
39-69.

White, H. (2000). A reality check for data snooping. Econometrica 68(5),
1097-1126.

Hansen, P. R. (2005). A test for superior predictive ability. Journal of
Business and Economic Statistics 23(4), 365-380.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from qvalid.contracts import FloatArray, PeriodReturns, TrialMatrix
from qvalid.core.constants import DEFAULT_CONFIDENCE_LEVEL, EULER_MASCHERONI
from qvalid.core.metrics import de_annualise_rate, dispersion_is_negligible
from qvalid.core.resample import estimate_block_length, stationary_bootstrap_indices
from qvalid.exceptions import InsufficientSampleError

__all__ = [
    "DEFAULT_CSCV_SPLITS",
    "DeflatedSharpe",
    "OverfitInputError",
    "PboResult",
    "ProbabilisticSharpe",
    "SpaResult",
    "TrackRecordLength",
    "deflated_sharpe_ratio",
    "expected_maximum_sharpe",
    "minimum_track_record_length",
    "probabilistic_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "superior_predictive_ability",
]

DEFAULT_CSCV_SPLITS = 16
"""Number of blocks in the combinatorially symmetric cross validation."""

_MIN_CONFIGS_FOR_PBO = 2
_MIN_TRIALS_FOR_DEFLATION = 2


class OverfitInputError(InsufficientSampleError):
    """A search correction was asked for without the input that makes it meaningful.

    Separate from a generic sample size error because the remedy is different.
    The user is not short of data; they have not declared how much searching
    they did. See D004.
    """


def _sample_moments(values: FloatArray) -> tuple[int, float, float, float]:
    """Return ``(T, per period Sharpe, skewness, kurtosis)`` on population moments."""
    n_obs = int(values.size)
    if n_obs < 3:
        raise InsufficientSampleError(
            "the third and fourth moments need at least three observations",
            observed=n_obs,
            threshold=3,
        )
    mean = float(values.mean())
    dispersion = float(values.std(ddof=0))
    if dispersion_is_negligible(values):
        # Not ``dispersion <= 0.0``: a constant series of a value that is not
        # representable in binary has a computed variance around 1e-38 rather
        # than zero, and the resulting Sharpe ratio is astronomical rather than
        # undefined. Same trap, same remedy, as in core/metrics.py.
        raise InsufficientSampleError(
            "the Sharpe ratio is undefined for a series of zero dispersion, so no "
            "correction for selection can be formed either",
            observed=dispersion,
            threshold=0.0,
        )
    centred = values - mean
    return (
        n_obs,
        mean / dispersion,
        float((centred**3).mean()) / dispersion**3,
        float((centred**4).mean()) / dispersion**4,
    )


@dataclass(frozen=True, slots=True)
class ProbabilisticSharpe:
    """Probability that the true Sharpe ratio exceeds a threshold.

    Attributes
    ----------
    probability : float
    observed_sharpe : float
        Per period, population convention. Not annualised, because the
        threshold and the observed value have to live on the same scale and the
        threshold is naturally quoted per period here.
    benchmark_sharpe : float
    n_periods : int
    skewness, kurtosis : float
    denominator : float
        ``sqrt(1 - g3 * SR + (g4 - 1) / 4 * SR^2)``, the standard deviation of
        the Sharpe estimator scaled by ``sqrt(T)``.

    Notes
    -----
    The denominator is algebraically identical to
    ``sqrt(T * mertens_sharpe_variance(values))``. That is not a coincidence
    and not an approximation: expanding
    ``1 + SR^2/2 - g3 SR + (g4-3)/4 SR^2`` gives
    ``1 - g3 SR + (g4-1)/4 SR^2`` exactly. A test asserts equality to ten
    decimals through both routes, which ties this module to ``core/metrics.py``
    rather than duplicating the formula.
    """

    probability: float
    observed_sharpe: float
    benchmark_sharpe: float
    n_periods: int
    skewness: float
    kurtosis: float
    denominator: float


def probabilistic_sharpe_ratio(
    excess: FloatArray, *, benchmark_sharpe: float = 0.0
) -> ProbabilisticSharpe:
    """Probability that the true per period Sharpe exceeds ``benchmark_sharpe``.

    Parameters
    ----------
    excess : numpy.ndarray of float64
        Per period returns, from a ``PeriodReturns`` or a column of a
        ``TrialMatrix``.
    benchmark_sharpe : float, optional
        Per period threshold. Defaults to zero and is carried on the result, so
        the default is declared rather than silent.

    Returns
    -------
    ProbabilisticSharpe

    Notes
    -----
    Bailey and López de Prado (2014):

    ``PSR = Phi( (SR - SR_b) * sqrt(T - 1) / sqrt(1 - g3 SR + (g4-1)/4 SR^2) )``

    Hypotheses. The returns are stationary and ergodic and the estimator of the
    Sharpe ratio is asymptotically normal. Normality of the returns themselves
    is **not** assumed; the third and fourth moments enter explicitly, which is
    the point of the correction.

    What it measures. The probability, under repeated sampling of the same
    process, that the true Sharpe exceeds the threshold, given the observed
    moments.

    What it does not measure. Anything about selection. A configuration chosen
    as the best of a thousand has a high probabilistic Sharpe by construction,
    which is exactly why :func:`deflated_sharpe_ratio` exists.
    """
    n_obs, sharpe, skewness, kurtosis = _sample_moments(excess)
    denominator = math.sqrt(
        max(1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe, 0.0)
    )
    if denominator <= 0.0:  # pragma: no cover
        # Unreachable in practice. The quantity is 1 - g3 SR + (g4-1)/4 SR^2,
        # which is the variance of the Sharpe estimator scaled by T and is non
        # negative wherever the moments come from a real sample. The guard
        # exists for the floating point corner and is clamped above it.
        raise InsufficientSampleError(
            "the estimated variance of the Sharpe estimator is non positive, which "
            "happens only for degenerate sample moments",
            observed=denominator,
            threshold=0.0,
        )
    statistic = (sharpe - benchmark_sharpe) * math.sqrt(n_obs - 1) / denominator
    return ProbabilisticSharpe(
        probability=float(norm.cdf(statistic)),
        observed_sharpe=sharpe,
        benchmark_sharpe=benchmark_sharpe,
        n_periods=n_obs,
        skewness=skewness,
        kurtosis=kurtosis,
        denominator=denominator,
    )


def expected_maximum_sharpe(n_trials: int, trial_variance: float) -> float:
    """Compute the expected maximum of ``N`` independent Sharpe ratios under a zero null.

    Parameters
    ----------
    n_trials : int
        Number of configurations tried. At least two.
    trial_variance : float
        Variance across the Sharpe ratios of those configurations, per period.

    Returns
    -------
    float
        ``sqrt(V) * [(1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N e))]``
        with ``gamma`` the Euler-Mascheroni constant.

    Notes
    -----
    This is the threshold a strategy has to clear merely to be the best of ``N``
    coin flips. It grows without bound in ``N``, slowly, which is the whole
    content of the correction: searching harder raises the bar.

    **Accuracy, measured rather than assumed.** The expression is a Gumbel
    approximation to the maximum of ``N`` normals and is asymptotic in ``N``.
    Ratio of the closed form to simulation over twenty thousand draws:

    ======  ======  ======  ======  ======  ======
    N=2     N=5     N=10    N=50    N=200   N=1000
    ======  ======  ======  ======  ======  ======
    1.064   0.977   0.972   0.987   0.994   0.995
    ======  ======  ======  ======  ======  ======

    So a user who declares five trials receives a number carrying a few per cent
    of approximation error, and that error comes from the formula rather than
    from the sample. With hundreds of trials, which is the regime the correction
    was designed for, the error is under one per cent.

    Independence is assumed across trials. Configurations from a parameter sweep
    are typically far from independent, so the effective number of trials is
    below the nominal count and this threshold is conservative in the wrong
    direction, that is, too low. Bailey and López de Prado discuss clustering as
    the remedy; it is not implemented here and the limitation is declared.
    """
    if n_trials < _MIN_TRIALS_FOR_DEFLATION:
        raise OverfitInputError(
            "the deflation needs at least two trials; with one configuration there was "
            "no search to correct for",
            observed=n_trials,
            threshold=_MIN_TRIALS_FOR_DEFLATION,
        )
    if trial_variance <= 0.0:
        raise OverfitInputError(
            "the deflation needs a positive variance across the trial Sharpe ratios; "
            "identical trials carry no information about the search",
            observed=trial_variance,
            threshold=0.0,
        )
    upper = float(norm.ppf(1.0 - 1.0 / n_trials))
    lower = float(norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
    return math.sqrt(trial_variance) * ((1.0 - EULER_MASCHERONI) * upper + EULER_MASCHERONI * lower)


@dataclass(frozen=True, slots=True)
class DeflatedSharpe:
    """Probabilistic Sharpe measured against the threshold the search itself sets.

    Attributes
    ----------
    probability : float
        The deflated Sharpe ratio: probability that the true Sharpe is above the
        expected maximum of the trials, not above zero.
    expected_maximum : float
        Per period Sharpe a strategy needs merely to be the best of ``n_trials``
        coin flips.
    n_trials : int
    trial_variance : float
    psr : ProbabilisticSharpe
        Against the deflated threshold, so ``psr.benchmark_sharpe`` equals
        ``expected_maximum``.
    psr_against_zero : float
        The undeflated probability, reported alongside so the size of the
        correction is visible instead of implicit.
    """

    probability: float
    expected_maximum: float
    n_trials: int
    trial_variance: float
    psr: ProbabilisticSharpe
    psr_against_zero: float


def deflated_sharpe_ratio(
    excess: FloatArray, *, n_trials: int, trial_variance: float
) -> DeflatedSharpe:
    """Probability that the true Sharpe survives the selection that produced it.

    Parameters
    ----------
    excess : numpy.ndarray of float64
        Per period returns of the selected configuration.
    n_trials : int
        Number of configurations tested. **Mandatory.** See D004: if the user
        does not know, the test does not run and the report states that no
        correction for search was applied. Estimating it by heuristic would be
        fabricating the input that determines the result.
    trial_variance : float
        Variance across the per period Sharpe ratios of those configurations.
        Available directly from a :class:`~qvalid.contracts.TrialMatrix`.

    Returns
    -------
    DeflatedSharpe

    Raises
    ------
    OverfitInputError
        Fewer than two trials, or non positive dispersion across them.

    Notes
    -----
    Bailey and López de Prado (2014). The observed Sharpe is compared against
    the expected maximum of ``n_trials`` draws rather than against zero, so the
    quantity answered is "does this beat what searching alone would produce"
    rather than "is this positive".

    Both numbers are returned. A configuration with an undeflated probability of
    0.99 and a deflated one of 0.30 is a configuration whose apparent edge is
    mostly the search, and showing only one of the two hides that.

    Invalid when. The trials are strongly dependent, which is the normal case
    for a parameter sweep. See the note in :func:`expected_maximum_sharpe`.
    """
    threshold = expected_maximum_sharpe(n_trials, trial_variance)
    deflated = probabilistic_sharpe_ratio(excess, benchmark_sharpe=threshold)
    undeflated = probabilistic_sharpe_ratio(excess, benchmark_sharpe=0.0)
    return DeflatedSharpe(
        probability=deflated.probability,
        expected_maximum=threshold,
        n_trials=n_trials,
        trial_variance=trial_variance,
        psr=deflated,
        psr_against_zero=undeflated.probability,
    )


@dataclass(frozen=True, slots=True)
class TrackRecordLength:
    """Sample length required to distinguish the observed Sharpe from a threshold.

    Attributes
    ----------
    attainable : bool
        Whether any finite length would do. ``False`` when the observed Sharpe
        does not exceed the benchmark, where the required length is infinite.
        That is a **result**, not a failure, and D064 records why the
        distinction is worth a field: an observed Sharpe below the threshold
        is decisive negative evidence, and filing it as an absent section
        inverts the rule of ``02`` section 7 that absence is never a verdict.
    periods : float or None
        In periods of the current grid. ``None`` when not attainable, so a
        caller that ignores :attr:`attainable` gets nothing rather than a
        number it can plan around.
    years : float or None
        The same quantity in calendar time, using ``periods_per_year``.
    observed_periods : int
    sufficient : bool
        Whether the sample already exceeds the requirement. Always ``False``
        when the requirement cannot be met at any length.
    observed_sharpe : float
        Per period, excess of the risk free rate. Kept because it is what the
        unattainable case is about, and a reader who sees no length wants to
        know how far below the benchmark it sat.
    target_probability : float
    benchmark_sharpe : float
    """

    attainable: bool
    periods: float | None
    years: float | None
    observed_periods: int
    sufficient: bool
    observed_sharpe: float
    target_probability: float
    benchmark_sharpe: float


def minimum_track_record_length(
    returns: PeriodReturns,
    *,
    risk_free_rate: float = 0.0,
    benchmark_sharpe: float = 0.0,
    target_probability: float = DEFAULT_CONFIDENCE_LEVEL,
) -> TrackRecordLength:
    """Minimum sample length for the observed Sharpe to clear a threshold.

    Parameters
    ----------
    returns : PeriodReturns
        Taken as a contract rather than a bare array, because the answer has to
        be converted into calendar time and that needs ``periods_per_year``.
    risk_free_rate : float, optional
        Simple annual rate, subtracted here so the Sharpe this measures is the
        **same quantity** the report's headline Sharpe is. Omitting it computed
        a raw Sharpe and answered a different question under the same name,
        which is the defect D055 found between three other sections.
    benchmark_sharpe : float, optional
        Per period threshold.
    target_probability : float, optional

    Returns
    -------
    TrackRecordLength

    Raises
    ------
    InsufficientSampleError
        If ``target_probability`` is not inside the open interval (0, 1), which
        is a bad argument rather than a fact about the returns.

        A Sharpe at or below the benchmark used to be raised here too. It is
        now returned with ``attainable=False``, because it is an answer and not
        an error; see D064 and :class:`TrackRecordLength`.

    Notes
    -----
    Bailey and López de Prado (2014):

    ``minTRL = 1 + [1 - g3 SR + (g4-1)/4 SR^2] * (Phi^-1(p) / (SR - SR_b))^2``

    The result is reported in periods of the current grid and converted to years
    for the report, per ``02`` section 3.2. It is not a forecast: it says how
    long a track record with these moments would need to be, not how long this
    strategy will take to prove itself, since the moments are themselves
    estimated.
    """
    per_period = de_annualise_rate(risk_free_rate, returns.periods_per_year)
    values = np.asarray(returns.values, dtype=np.float64) - per_period
    _, sharpe, skewness, kurtosis = _sample_moments(values)
    if not 0.0 < target_probability < 1.0:
        raise InsufficientSampleError(
            "target probability must lie in the open interval (0, 1)",
            observed=target_probability,
            threshold=(0.0, 1.0),
        )
    if sharpe <= benchmark_sharpe:
        # Not an error. No finite length makes a Sharpe at or below the
        # threshold significantly above it, so the answer is infinity, and
        # infinity is the most informative thing this function ever says. It
        # used to be raised as InsufficientSampleError, whose name told the
        # reader to collect more data while its own message told them not to.
        return TrackRecordLength(
            attainable=False,
            periods=None,
            years=None,
            observed_periods=returns.n_periods,
            sufficient=False,
            observed_sharpe=sharpe,
            target_probability=target_probability,
            benchmark_sharpe=benchmark_sharpe,
        )
    variance_factor = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    quantile = float(norm.ppf(target_probability))
    periods = 1.0 + variance_factor * (quantile / (sharpe - benchmark_sharpe)) ** 2
    return TrackRecordLength(
        attainable=True,
        periods=periods,
        years=periods / returns.periods_per_year,
        observed_periods=returns.n_periods,
        sufficient=returns.n_periods >= periods,
        observed_sharpe=sharpe,
        target_probability=target_probability,
        benchmark_sharpe=benchmark_sharpe,
    )


@dataclass(frozen=True, slots=True)
class PboResult:
    """Probability that the in sample winner is below median out of sample.

    Attributes
    ----------
    probability : float
        Fraction of splits whose in sample best ranked below the out of sample
        median. Half means the winner is a coin flip; zero means the ranking
        carries over.
    logits : numpy.ndarray of float64
        One per split. Their median is a compact summary of how far the winner
        sits from the median out of sample.
    median_logit : float
    n_splits : int
    n_combinations : int
    n_configs : int
    periods_used : int
        Rows actually used. The tail that does not divide evenly into blocks is
        dropped, and the count is reported rather than absorbed.

    Notes
    -----
    The interpretation of a probability near one half is worth stating, because
    it looks like a failure of the method and is not. Under pure noise the
    configuration that wins in sample is no better than a coin flip out of
    sample, so one half **is** the correct answer and is what the test suite
    pins. Measured over four seeds at ``T = 1000``, ``N = 50`` and ``S = 16``,
    the probability under noise averages 0.475 and still ranges from 0.26 to
    0.58, so a single run is not enough to characterise it.

    The logit is bounded by ``+/- log(N)``, since the best possible relative
    rank is ``N / (N + 1)``. With fifty configurations the ceiling is 3.912, and
    a strategy whose edge is unambiguous sits exactly there. The consequence is
    that the magnitude of the median logit is **not** comparable across
    universes of different size, only its sign and its distance from zero
    relative to that ceiling.
    """

    probability: float
    logits: FloatArray
    median_logit: float
    n_splits: int
    n_combinations: int
    n_configs: int
    periods_used: int


def _split_sharpe(block: FloatArray) -> FloatArray:
    """Per period Sharpe of each column, sample convention, no HAC.

    Declared divergence from ``02`` section 1.2, which specifies a long run
    variance estimator. A cross validation split concatenates blocks that are
    not adjacent in time, so lag ``k`` inside the concatenation is not lag ``k``
    in the calendar and the Bartlett weights would be applied to autocovariances
    that do not exist. Bailey, Borwein, López de Prado and Zhu (2017) use the
    plain ratio for the same reason.
    """
    dispersion = block.std(axis=0, ddof=1)
    safe = np.where(dispersion > 0.0, dispersion, 1.0)
    return np.where(dispersion > 0.0, block.mean(axis=0) / safe, 0.0)


def probability_of_backtest_overfitting(
    trials: TrialMatrix, *, n_splits: int = DEFAULT_CSCV_SPLITS
) -> PboResult:
    """Probability of backtest overfitting by combinatorially symmetric cross validation.

    Parameters
    ----------
    trials : TrialMatrix
        Every configuration tested, on the shared grid.
    n_splits : int, optional
        Number of blocks ``S``, must be even. The procedure evaluates all
        ``C(S, S/2)`` ways of splitting them into halves, which is 12870 for the
        default of 16.

    Returns
    -------
    PboResult

    Raises
    ------
    InsufficientSampleError
        Odd ``n_splits``, fewer than two configurations, or fewer periods than
        blocks.

    Notes
    -----
    Bailey, Borwein, López de Prado and Zhu (2017). Partition the sample into
    ``S`` blocks; for every way of choosing ``S/2`` of them as the in sample
    set, find the configuration with the highest Sharpe there, look up its rank
    among all configurations on the complementary set, and record the logit of
    the relative rank. The probability is the fraction of splits whose logit is
    at or below zero.

    Symmetric means that every split is used once as in sample and once as out
    of sample, which removes the arbitrariness of a single train and test cut
    and is why the estimate does not depend on where the history happens to be
    divided.

    **Measured behaviour**, on ``T = 1000``, ``N = 50`` and ``S = 16``:

    ==============================  ======  ================
    scenario                        PBO     median logit
    ==============================  ======  ================
    pure noise                      0.510   -0.039
    real edge in one configuration  0.030   +3.912
    edge in five of fifty           0.061
    ==============================  ======  ================

    Hypotheses. The configurations are comparable, that is, computed on the same
    grid over the same periods, which the contract guarantees. Blocks are
    treated as exchangeable, so a strong trend in the level of performance over
    the sample violates the setup.

    What it does not measure. Whether the strategy has an edge. It measures
    whether the *selection procedure* generalises. A universe of uniformly good
    configurations gives a high probability, correctly, because picking the best
    of them is then indeed a coin flip.
    """
    if n_splits % 2 != 0:
        raise InsufficientSampleError(
            "the number of blocks must be even so that the splits are symmetric",
            observed=n_splits,
            threshold="even",
        )
    if trials.n_configs < _MIN_CONFIGS_FOR_PBO:
        raise InsufficientSampleError(
            "cross validation of a selection needs at least two configurations to select between",
            observed=trials.n_configs,
            threshold=_MIN_CONFIGS_FOR_PBO,
        )
    block_size = trials.n_periods // n_splits
    if block_size < 2:
        raise InsufficientSampleError(
            f"{trials.n_periods} periods split into {n_splits} blocks leaves fewer than "
            "two observations per block, so a Sharpe ratio cannot be formed on a split",
            observed=block_size,
            threshold=2,
        )

    used = block_size * n_splits
    blocks = np.stack(np.split(np.asarray(trials.values)[:used], n_splits))
    n_configs = trials.n_configs
    all_blocks = set(range(n_splits))
    logits: list[float] = []
    for train in itertools.combinations(range(n_splits), n_splits // 2):
        test = sorted(all_blocks - set(train))
        in_sample = blocks[list(train)].reshape(-1, n_configs)
        out_sample = blocks[test].reshape(-1, n_configs)
        best = int(np.argmax(_split_sharpe(in_sample)))
        out_scores = _split_sharpe(out_sample)
        rank = float((out_scores <= out_scores[best]).sum())
        relative = rank / (n_configs + 1.0)
        logits.append(math.log(relative / (1.0 - relative)))

    logit_array = np.ascontiguousarray(logits, dtype=np.float64)
    return PboResult(
        probability=float((logit_array <= 0.0).mean()),
        logits=logit_array,
        median_logit=float(np.median(logit_array)),
        n_splits=n_splits,
        n_combinations=int(logit_array.size),
        n_configs=n_configs,
        periods_used=used,
    )


@dataclass(frozen=True, slots=True)
class SpaResult:
    """Test of superiority over a benchmark, corrected for the size of the universe.

    Attributes
    ----------
    p_value_lower, p_value_consistent, p_value_upper : float
        The three recentrings of Hansen (2005). They always satisfy
        ``lower <= consistent <= upper``, which a test asserts on every
        replication rather than trusting the argument.
    p_value_reality_check : float
        White (2000), the unstudentised statistic under the least favourable
        null. Reported for comparison, per ``02`` section 3.4.
    statistic : float
        The observed studentised maximum.
    best_config : str
    n_configs, n_periods, n_bootstrap : int
    block_length : float
    seed : int

    Notes
    -----
    Read ``p_value_consistent``. The other two bracket it and exist so the
    bracket is visible.

    **Measured size and power**, ``n = 500``, ``K = 20``, 200 replications:

    =================================  =====  ==========  =====  =============
    scenario                           lower  consistent  upper  reality check
    =================================  =====  ==========  =====  =============
    size under the null, nominal 0.05  0.110  0.090       0.090  0.050
    power, one model with an edge      0.755  0.710       0.710  0.710
    power with poor models present     0.960  0.960       0.750  0.745
    =================================  =====  ==========  =====  =============

    The last row is the reason the SPA exists. When the universe contains models
    that are clearly worse than the benchmark, the reality check has to treat
    them as if they might be at the boundary, which inflates its critical value
    and costs power. The consistent recentring drops them and recovers it, 0.960
    against 0.745.

    The first row is a limitation and is stated rather than buried: the
    studentised variants over reject at ``n = 500``, nine per cent against a
    nominal five. The unstudentised reality check is correctly sized there. The
    distortion is a finite sample effect and shrinks with the sample.
    """

    p_value_lower: float
    p_value_consistent: float
    p_value_upper: float
    p_value_reality_check: float
    statistic: float
    best_config: str
    n_configs: int
    n_periods: int
    n_bootstrap: int
    block_length: float
    seed: int


def superior_predictive_ability(
    trials: TrialMatrix,
    benchmark: FloatArray,
    *,
    seed: int,
    n_bootstrap: int = 1_000,
    block_length: float | None = None,
) -> SpaResult:
    """Test whether the best configuration beats a benchmark, correcting for the search.

    Parameters
    ----------
    trials : TrialMatrix
    benchmark : numpy.ndarray of float64
        Benchmark return per period, same length as the matrix. A vector of
        zeros tests superiority over holding cash.
    seed : int
        Mandatory.
    n_bootstrap : int, optional
        Bootstrap replications. Explicit and reported.
    block_length : float or None, optional
        ``None`` estimates it from the loss differential of the best
        configuration by :func:`~qvalid.core.resample.estimate_block_length`.

    Returns
    -------
    SpaResult

    Raises
    ------
    InsufficientSampleError
        Length mismatch, or a sample too short for the block length guard.

    Notes
    -----
    Hansen (2005), with White (2000) as the comparison. The null is that no
    configuration beats the benchmark. The statistic is the studentised maximum
    of the mean loss differentials, and its distribution comes from a stationary
    bootstrap.

    **One implementation point decides whether this works.** The same bootstrap
    index matrix must resample every configuration simultaneously. Drawing
    independent indices per configuration would destroy the cross sectional
    dependence between them, and the distribution of the maximum over
    configurations is precisely where that dependence matters. The error
    produces a test that looks reasonable and is wrong, so it is worth naming.

    The recentring is

    ``T*_b = max_k max( sqrt(n) (dbar*_k - dbar_k + mu_k) / omega_k , 0 )``

    that is, subtract the sample mean and **add** the mean estimated under the
    null. Subtracting ``mu_k`` directly, which is the natural misreading,
    produces a test with zero power; it was written that way first here and the
    size and power measurement is what caught it.
    """
    values = np.asarray(trials.values, dtype=np.float64)
    benchmark_values = np.asarray(benchmark, dtype=np.float64)
    if benchmark_values.shape != (trials.n_periods,):
        raise InsufficientSampleError(
            "the benchmark must cover exactly the periods of the trial matrix",
            observed=benchmark_values.shape,
            threshold=(trials.n_periods,),
        )

    differential = values - benchmark_values[:, None]
    n_obs = trials.n_periods
    mean_differential = differential.mean(axis=0)
    best = int(np.argmax(mean_differential))

    if block_length is None:
        estimate = estimate_block_length(
            np.ascontiguousarray(differential[:, best], dtype=np.float64)
        )
        chosen_block = estimate.block_length
    else:
        chosen_block = float(block_length)

    index = stationary_bootstrap_indices(
        n_obs, n_paths=n_bootstrap, n_steps=n_obs, block_length=chosen_block, seed=seed
    )
    replicates = differential[index].mean(axis=1)
    omega = np.sqrt(n_obs * replicates.var(axis=0, ddof=1))
    omega = np.where(omega > 0.0, omega, np.inf)

    root_n = math.sqrt(n_obs)
    statistic = float(np.max(np.maximum(root_n * mean_differential / omega, 0.0)))
    threshold = omega / root_n * math.sqrt(2.0 * math.log(math.log(n_obs)))
    centred = replicates - mean_differential

    def p_value(null_mean: FloatArray) -> float:
        draws = np.max(np.maximum(root_n * (centred + null_mean) / omega, 0.0), axis=1)
        return float((draws >= statistic).mean())

    reality_check_statistic = float(np.max(root_n * mean_differential))
    reality_check_draws = np.max(root_n * centred, axis=1)

    return SpaResult(
        p_value_lower=p_value(np.minimum(mean_differential, 0.0)),
        p_value_consistent=p_value(
            np.where(mean_differential < -threshold, mean_differential, 0.0)
        ),
        p_value_upper=p_value(np.zeros_like(mean_differential)),
        p_value_reality_check=float((reality_check_draws >= reality_check_statistic).mean()),
        statistic=statistic,
        best_config=str(trials.config_ids[best]),
        n_configs=trials.n_configs,
        n_periods=n_obs,
        n_bootstrap=n_bootstrap,
        block_length=chosen_block,
        seed=seed,
    )
