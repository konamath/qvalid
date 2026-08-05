"""Causal regime labelling, attribution by state, and Markov resampling.

The question this module answers is the third of the three in ``01``: under
which market conditions does the result hold. A strategy whose whole P&L comes
from one corner of the grid is a bet on that corner, whatever its Sharpe ratio
says.

Everything here is causal by construction. The quantile cuts that define the
states are estimated on an expanding window, so the label of a period uses only
information available before it. A quantile computed over the whole sample is
look ahead and invalidates every statistic downstream, which is why ``02``
section 4 calls it a hard rule and why the test suite checks it two ways: a
prefix of the series must produce a prefix of the labels, and perturbing the
future must leave the past untouched.

The label of period ``t`` comes from a window ending at ``t - 1``. Including the
period's own return would not be look ahead in the strict sense, but it would
create a mechanical correlation: for a long only strategy an up period would be
labelled as an up trend and credited with profit in the same breath, and the
attribution would measure the direction of the position rather than the regime.
See D026.

References
----------
Welch, B. L. (1951). On the comparison of several mean values: an alternative
approach. Biometrika 38(3/4), 330-336.

Bailey, D. H., Borwein, J., López de Prado, M., and Zhu, Q. J. (2017). The
probability of backtest overfitting. Journal of Computational Finance 20(4),
39-69.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import f as f_distribution

from qvalid.contracts import (
    UNDEFINED_STATE,
    Basis,
    EquityPaths,
    FloatArray,
    IntArray,
    PeriodReturns,
    RegimeLabels,
    Unit,
)
from qvalid.core.constants import MIN_STATE_OBS
from qvalid.exceptions import InsufficientSampleError, LookaheadError, RegimeSparsityError

__all__ = [
    "DEFAULT_REGIME_WINDOW",
    "DEFAULT_STATES_PER_AXIS",
    "MARKOV_RESAMPLE",
    "RegimeAttribution",
    "TransitionMatrix",
    "WelchAnova",
    "attribute_by_regime",
    "expanding_quantile_states",
    "label_regimes",
    "markov_resample",
    "transition_matrix",
    "welch_anova",
]

DEFAULT_STATES_PER_AXIS = 3
"""Suggested grid, three by three, giving nine states. See ``02`` section 4."""

DEFAULT_REGIME_WINDOW = 21
"""Trailing window in periods. One civil month of daily sessions."""

MARKOV_RESAMPLE = "markov_regime_resample"
"""Method identifier stamped on :class:`~qvalid.contracts.EquityPaths`."""


def expanding_quantile_states(values: FloatArray, n_states: int, warmup: int) -> IntArray:
    """Classify a series into quantile buckets using only its own past.

    Parameters
    ----------
    values : numpy.ndarray of float64
        The statistic to be bucketed, already causal, one entry per period.
    n_states : int
        Number of buckets, at least two.
    warmup : int
        Leading periods left undefined. Must be at least ``n_states``.

    Returns
    -------
    numpy.ndarray of int64
        Bucket index in ``[0, n_states - 1]``, or
        :data:`~qvalid.contracts.UNDEFINED_STATE` during the warm up.

    Notes
    -----
    The cuts for period ``t`` come from ``values[:t]``, strictly before ``t``.
    That is the whole content of the causality guarantee: the classification of
    an observation never uses the observation itself, let alone anything after
    it.

    Cost, measured rather than assumed, per the performance rule of ``04``. A
    fresh ``numpy.quantile`` per period gives 0.012 seconds at ``T = 500``,
    0.029 at 1000, 0.073 at 2000 and 0.207 at 4000. Quadratic in ``T`` and
    irrelevant at any sample this library handles, so step 1 of the ladder in
    ``04`` is where this stops. A running order statistic would be the next
    step and does not enter without a measurement demanding it.
    """
    if n_states < 2:
        raise InsufficientSampleError(
            "a regime axis needs at least two states", observed=n_states, threshold=2
        )
    if warmup < n_states:
        raise InsufficientSampleError(
            "the warm up must be at least as long as the number of states, or the "
            "first quantile cuts are estimated from fewer points than there are buckets",
            observed=warmup,
            threshold=n_states,
        )
    n_obs = int(values.size)
    states = np.full(n_obs, UNDEFINED_STATE, dtype=np.int64)
    probabilities = np.arange(1, n_states) / n_states
    for index in range(warmup, n_obs):
        cuts = np.quantile(values[:index], probabilities)
        states[index] = int(np.searchsorted(cuts, values[index], side="right"))
    return states


def label_regimes(
    reference: FloatArray,
    period_end_ns: IntArray,
    *,
    reference_id: str,
    n_trend_states: int = DEFAULT_STATES_PER_AXIS,
    n_volatility_states: int = DEFAULT_STATES_PER_AXIS,
    window: int = DEFAULT_REGIME_WINDOW,
) -> RegimeLabels:
    """Label every period on a two dimensional grid of trend and volatility.

    Parameters
    ----------
    reference : numpy.ndarray of float64
        Per period returns of the reference market series, on the same grid as
        the strategy. Supplied as an argument and never fetched, per ``01``.
    period_end_ns : numpy.ndarray of int64
    reference_id : str
        Which series this is. It enters the report, because relabelling against
        a different reference changes every attribution.
    n_trend_states, n_volatility_states : int, optional
        Buckets per axis. ``02`` section 4 suggests three by three and reducing
        to two by two on a small sample.
    window : int, optional
        Trailing window in periods for both estimators. Declared and reported,
        never silent, because it changes every label.

    Returns
    -------
    RegimeLabels

    Raises
    ------
    InsufficientSampleError
        Sample shorter than the warm up, or a window below two periods.

    Notes
    -----
    Trend estimator: mean return over the trailing window. Volatility
    estimator: standard deviation over the same window. Both end at ``t - 1``,
    so the label is knowable before the period starts.

    Because the axes are classified by quantile, the labels depend only on the
    **ordering** of the estimator within the expanding sample. Any strictly
    increasing transform of the trend estimator produces identical labels, and
    a test asserts it. That is a useful property to know: arguing about whether
    to use the mean, the sum, or the mean divided by the window length is
    arguing about nothing.

    The warm up is ``max(window, states_per_axis * MIN_STATE_OBS)``, that is 60
    periods on a three by three grid. The derivation reuses ``MIN_STATE_OBS``:
    below twenty observations per bucket the quantile cuts are noise, and
    labelling anyway would inject that noise into the attribution with no way to
    separate it from a real state.
    """
    values = np.ascontiguousarray(reference, dtype=np.float64)
    if window < 2:
        raise InsufficientSampleError(
            "the regime window must span at least two periods", observed=window, threshold=2
        )
    warmup = max(window, max(n_trend_states, n_volatility_states) * MIN_STATE_OBS)
    if values.size <= warmup:
        raise InsufficientSampleError(
            f"labelling needs more than the warm up of {warmup} periods, since every "
            "period inside it is undefined by construction",
            observed=int(values.size),
            threshold=warmup + 1,
        )

    n_obs = int(values.size)
    drift = np.full(n_obs, np.nan)
    dispersion = np.full(n_obs, np.nan)
    for index in range(window, n_obs):
        past = values[index - window : index]
        drift[index] = past.mean()
        dispersion[index] = past.std(ddof=1)

    filled_drift = np.where(np.isnan(drift), 0.0, drift)
    filled_dispersion = np.where(np.isnan(dispersion), 0.0, dispersion)
    trend = expanding_quantile_states(filled_drift, n_trend_states, warmup)
    volatility = expanding_quantile_states(filled_dispersion, n_volatility_states, warmup)

    return RegimeLabels(
        trend=np.ascontiguousarray(trend, dtype=np.int8),
        volatility=np.ascontiguousarray(volatility, dtype=np.int8),
        period_end_ns=np.ascontiguousarray(period_end_ns, dtype=np.int64),
        n_trend_states=n_trend_states,
        n_volatility_states=n_volatility_states,
        window=window,
        warmup=warmup,
        reference_id=reference_id,
    )


@dataclass(frozen=True, slots=True)
class WelchAnova:
    """Equality of means across groups, without assuming equal variances.

    Attributes
    ----------
    statistic : float
    p_value : float
    df_between, df_within : float
    n_groups : int
    group_sizes : tuple of int

    Notes
    -----
    Welch (1951). The standard analysis of variance assumes equal variances
    across groups, and on this grid that assumption is false **by
    construction**: one axis of the grid is volatility, so the states differ in
    variance by design, and they differ in count as well.

    Measured, with genuinely equal means and standard deviations of 1, 3 and 9
    over 2000 replications:

    ==============================  ======  ============  =======
    setup                           Welch   ``f_oneway``  nominal
    ==============================  ======  ============  =======
    equal counts                    0.0580  0.0870        0.05
    counts 40, 100, 300             0.0410  0.0005        0.05
    ==============================  ======  ============  =======

    The second row is the argument. With the larger count in the higher variance
    state, which is the usual arrangement here, the equal variance test almost
    never rejects. Using it would report "no difference between regimes" for
    reasons that have nothing to do with the regimes.
    """

    statistic: float
    p_value: float
    df_between: float
    df_within: float
    n_groups: int
    group_sizes: tuple[int, ...]


def welch_anova(groups: list[FloatArray]) -> WelchAnova:
    """Test equality of means across groups under unequal variances and counts.

    Parameters
    ----------
    groups : list of numpy.ndarray of float64
        At least two groups, each with at least two observations and non zero
        dispersion.

    Returns
    -------
    WelchAnova

    Raises
    ------
    InsufficientSampleError
        Fewer than two usable groups, or a group with no dispersion, where the
        weight ``n / s^2`` is unbounded.
    """
    usable = [np.asarray(g, dtype=np.float64) for g in groups if np.asarray(g).size >= 2]
    if len(usable) < 2:
        raise InsufficientSampleError(
            "comparing means needs at least two groups holding at least two observations each",
            observed=len(usable),
            threshold=2,
        )
    counts = np.array([g.size for g in usable], dtype=np.float64)
    means = np.array([g.mean() for g in usable])
    variances = np.array([g.var(ddof=1) for g in usable])
    if bool((variances <= 0.0).any()):
        raise InsufficientSampleError(
            "a group with zero dispersion carries unbounded weight in the Welch statistic, "
            "so the test is undefined rather than merely extreme",
            observed=float(variances.min()),
            threshold=0.0,
        )

    n_groups = len(usable)
    weights = counts / variances
    total_weight = float(weights.sum())
    grand_mean = float((weights * means).sum()) / total_weight
    numerator = float((weights * (means - grand_mean) ** 2).sum()) / (n_groups - 1)
    lam = float((((1.0 - weights / total_weight) ** 2) / (counts - 1.0)).sum())
    denominator = 1.0 + 2.0 * (n_groups - 2) / (n_groups * n_groups - 1) * lam
    statistic = numerator / denominator
    df_within = (n_groups * n_groups - 1) / (3.0 * lam)
    return WelchAnova(
        statistic=statistic,
        p_value=float(f_distribution.sf(statistic, n_groups - 1, df_within)),
        df_between=float(n_groups - 1),
        df_within=df_within,
        n_groups=n_groups,
        group_sizes=tuple(int(c) for c in counts),
    )


@dataclass(frozen=True, slots=True)
class RegimeAttribution:
    """Where the P&L came from, by state, with the test of equality of means.

    Attributes
    ----------
    totals, means, counts : dict of int to float or int
        Keyed by joint state index.
    undefined_periods : int
        Periods inside the warm up, excluded from every figure above.
    undefined_total : float
        Their P&L, reported rather than absorbed, so the attribution adds up.
    concentration : float
        Share of the total absolute P&L held by the single largest state. One
        means the entire result comes from one corner of the grid.
    equality_of_means : WelchAnova or None
        ``None`` when fewer than two states hold enough observations.
    reference_id : str
    warnings : tuple of str

    Notes
    -----
    The question ``02`` section 4 says matters is whether the result comes from
    all states or from one, so ``concentration`` is the headline and the per
    state table is the evidence. A strategy with a fine Sharpe ratio and a
    concentration of 0.9 is a bet on one regime, and the Sharpe ratio has no way
    of saying so.
    """

    totals: dict[int, float]
    means: dict[int, float]
    counts: dict[int, int]
    undefined_periods: int
    undefined_total: float
    concentration: float
    equality_of_means: WelchAnova | None
    reference_id: str
    warnings: tuple[str, ...]


def attribute_by_regime(returns: PeriodReturns, labels: RegimeLabels) -> RegimeAttribution:
    """Split the P&L across regime states and test whether the means differ.

    Parameters
    ----------
    returns : PeriodReturns
    labels : RegimeLabels
        Must cover exactly the same periods, checked by comparing the closing
        instants rather than by trusting the lengths.

    Returns
    -------
    RegimeAttribution

    Raises
    ------
    LookaheadError
        If the labels do not align with the returns period by period. That is
        raised as a look ahead error rather than a schema error because a
        misalignment of one period is exactly how future information leaks into
        a label without anyone noticing.
    """
    values = np.asarray(returns.values, dtype=np.float64)
    if labels.n_periods != returns.n_periods or not bool(
        np.array_equal(np.asarray(labels.period_end_ns), np.asarray(returns.period_end_ns))
    ):
        raise LookaheadError(
            "regime labels and returns must cover exactly the same periods; a shift of "
            "even one period is how a label acquires information from the future it was "
            f"built to exclude. Labels cover {labels.n_periods} periods, returns "
            f"{returns.n_periods}"
        )

    joint = np.asarray(labels.joint())
    defined = joint != UNDEFINED_STATE
    warnings: list[str] = []

    totals: dict[int, float] = {}
    means: dict[int, float] = {}
    counts: dict[int, int] = {}
    groups: list[FloatArray] = []
    for state in sorted({int(s) for s in joint[defined]}):
        mask = joint == state
        bucket = values[mask]
        totals[state] = float(bucket.sum())
        means[state] = float(bucket.mean())
        counts[state] = int(bucket.size)
        if bucket.size >= 2 and float(bucket.var(ddof=1)) > 0.0:
            groups.append(np.ascontiguousarray(bucket))

    absolute = {state: abs(total) for state, total in totals.items()}
    denominator = sum(absolute.values())
    concentration = max(absolute.values()) / denominator if denominator > 0.0 else 0.0

    equality: WelchAnova | None = None
    if len(groups) >= 2:
        equality = welch_anova(groups)
    else:
        warnings.append(
            f"only {len(groups)} states hold enough dispersion to compare, so no test of "
            "equality of means was run; absence of a test is not evidence of equality"
        )

    thin = {state: count for state, count in counts.items() if count < MIN_STATE_OBS}
    if thin:
        warnings.append(
            f"states {sorted(thin)} hold fewer than MIN_STATE_OBS={MIN_STATE_OBS} periods, "
            "so their means are estimated from too little to be compared"
        )

    return RegimeAttribution(
        totals=totals,
        means=means,
        counts=counts,
        undefined_periods=int((~defined).sum()),
        undefined_total=float(values[~defined].sum()),
        concentration=concentration,
        equality_of_means=equality,
        reference_id=labels.reference_id,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class TransitionMatrix:
    """Empirical first order transition probabilities between joint states.

    Attributes
    ----------
    matrix : numpy.ndarray of float64, shape ``(n_states, n_states)``
        Row ``i`` sums to one and holds ``P(next = j | current = i)``.
    counts : numpy.ndarray of int64
        Raw transition counts, so the reader can see how thin each row is.
    states : tuple of int
        Occupied joint states, in the order of the rows.
    n_transitions : int
    collapsed : bool
        Whether the grid was reduced before estimating.

    Notes
    -----
    Hypotheses. First order Markov over the states, and returns conditionally
    exchangeable within a state. The first is strong: a regime that has lasted
    twenty periods is treated exactly like one that started yesterday. The
    second forbids any within state trend.
    """

    matrix: FloatArray
    counts: IntArray
    states: tuple[int, ...]
    n_transitions: int
    collapsed: bool


def transition_matrix(labels: RegimeLabels, *, allow_collapse: bool = False) -> TransitionMatrix:
    """Estimate the transition matrix between joint regime states.

    Parameters
    ----------
    labels : RegimeLabels
    allow_collapse : bool, optional
        When a state holds fewer than ``MIN_STATE_OBS`` observations, ``02``
        section 2.2 requires collapsing the grid before simulating and raising
        :class:`~qvalid.exceptions.RegimeSparsityError` if the collapse is not
        authorised. Setting this to ``True`` authorises it: each axis is
        reduced to two states by merging around the median.

    Returns
    -------
    TransitionMatrix

    Raises
    ------
    RegimeSparsityError
        A state below the minimum with ``allow_collapse`` left at ``False``,
        or still below it after collapsing.
    """
    joint: IntArray = np.asarray(labels.joint())
    collapsed = False
    counts = labels.state_counts()
    thin = {state: count for state, count in counts.items() if count < MIN_STATE_OBS}
    if thin and allow_collapse:
        joint = _collapse_joint(labels)
        collapsed = True
        occupied = joint[joint != UNDEFINED_STATE]
        counts = {
            int(state): int(count)
            for state, count in zip(*np.unique(occupied, return_counts=True), strict=True)
        }
        thin = {state: count for state, count in counts.items() if count < MIN_STATE_OBS}
    if thin:
        raise RegimeSparsityError(
            f"states {sorted(thin)} hold fewer than {MIN_STATE_OBS} observations, so their "
            "rows of the transition matrix have standard errors of the same order as their "
            "entries and conditional resampling degenerates into repeating a few points"
            + ("; collapsing the grid did not fix it" if collapsed else ""),
            observed=min(thin.values()),
            threshold=MIN_STATE_OBS,
            detail="pass allow_collapse=True to merge each axis around its median"
            if not collapsed
            else "",
        )

    states = tuple(sorted(counts))
    lookup = {state: position for position, state in enumerate(states)}
    size = len(states)
    raw = np.zeros((size, size), dtype=np.int64)
    current = joint[:-1]
    following = joint[1:]
    valid = (current != UNDEFINED_STATE) & (following != UNDEFINED_STATE)
    for source, target in zip(current[valid], following[valid], strict=True):
        raw[lookup[int(source)], lookup[int(target)]] += 1

    row_totals = raw.sum(axis=1, keepdims=True)
    if bool((row_totals == 0).any()):  # pragma: no cover
        # Unreachable after the MIN_STATE_OBS guard above: a state holding at
        # least twenty observations has at least nineteen successors, so its row
        # cannot be empty. The guard stays because it is the invariant the row
        # normalisation below depends on.
        raise RegimeSparsityError(
            "a state is never left, so its row of the transition matrix is empty and the "
            "chain is not irreducible over the observed sample",
            observed=0,
            threshold=1,
        )
    return TransitionMatrix(
        matrix=np.ascontiguousarray(raw / row_totals, dtype=np.float64),
        counts=np.ascontiguousarray(raw),
        states=states,
        n_transitions=int(raw.sum()),
        collapsed=collapsed,
    )


def _collapse_joint(labels: RegimeLabels) -> IntArray:
    """Merge each axis into two states, splitting at the middle bucket."""
    trend = np.asarray(labels.trend, dtype=np.int64)
    volatility = np.asarray(labels.volatility, dtype=np.int64)
    undefined = (trend == UNDEFINED_STATE) | (volatility == UNDEFINED_STATE)
    trend_binary = (trend >= labels.n_trend_states / 2.0).astype(np.int64)
    volatility_binary = (volatility >= labels.n_volatility_states / 2.0).astype(np.int64)
    joint = trend_binary * 2 + volatility_binary
    return np.ascontiguousarray(np.where(undefined, UNDEFINED_STATE, joint))


def markov_resample(
    returns: PeriodReturns,
    labels: RegimeLabels,
    *,
    n_paths: int,
    seed: int,
    n_steps: int | None = None,
    allow_collapse: bool = False,
) -> EquityPaths:
    """Resample returns conditionally on a simulated chain of regime states.

    Parameters
    ----------
    returns : PeriodReturns
    labels : RegimeLabels
    n_paths : int
    seed : int
        Mandatory.
    n_steps : int or None, optional
        Path length in periods. ``None`` uses the observed length.
    allow_collapse : bool, optional
        Passed to :func:`transition_matrix`.

    Returns
    -------
    EquityPaths
        Absolute equity levels, built by the same basis rule as
        ``metrics.equity_curve`` and ``resample.resample_equity_paths``, so the
        three are directly comparable.

    Raises
    ------
    RegimeSparsityError
        Propagated from :func:`transition_matrix`.

    Notes
    -----
    ``02`` section 2.2. The chain is simulated from the empirical transition
    matrix and a return is drawn uniformly from the observations belonging to
    the current state. This preserves the temporal clustering of good and bad
    conditions, which the stationary bootstrap of ``core/resample.py`` only
    captures to the extent that clustering shows up as short range dependence.

    What it adds over the stationary bootstrap. Regimes persist for many periods
    and a block of a dozen observations cannot express that. What it costs. A
    first order Markov assumption on the states and conditional exchangeability
    within them, both stronger than weak stationarity.

    The initial state is drawn from the empirical occupation frequencies rather
    than fixed, so the simulation does not inherit whichever state the sample
    happened to start in.
    """
    values = np.asarray(returns.values, dtype=np.float64)
    if labels.n_periods != returns.n_periods:
        raise LookaheadError(
            "regime labels and returns must cover the same periods, got "
            f"{labels.n_periods} and {returns.n_periods}"
        )
    transitions = transition_matrix(labels, allow_collapse=allow_collapse)
    joint = _collapse_joint(labels) if transitions.collapsed else np.asarray(labels.joint())

    pools = [np.ascontiguousarray(values[joint == state]) for state in transitions.states]
    if any(pool.size == 0 for pool in pools):  # pragma: no cover
        # Same reason: the states come from the transition matrix, which already
        # refused any state below MIN_STATE_OBS observations.
        raise RegimeSparsityError(
            "a state of the transition matrix holds no returns to draw from",
            observed=0,
            threshold=1,
        )

    steps = returns.n_periods if n_steps is None else int(n_steps)
    if n_paths < 1 or steps < 1:
        raise InsufficientSampleError(
            "n_paths and n_steps must both be strictly positive",
            observed=(n_paths, steps),
            threshold=(1, 1),
        )

    rng = np.random.default_rng(seed)
    size = len(transitions.states)
    occupancy = np.array([pool.size for pool in pools], dtype=np.float64)
    occupancy /= occupancy.sum()
    cumulative = np.cumsum(np.asarray(transitions.matrix), axis=1)

    state = rng.choice(size, size=n_paths, p=occupancy)
    drawn = np.empty((n_paths, steps), dtype=np.float64)
    for step in range(steps):
        uniforms = rng.random(n_paths)
        for index in range(size):
            mask = state == index
            if not bool(mask.any()):
                continue
            drawn[mask, step] = pools[index][
                rng.integers(0, pools[index].size, size=int(mask.sum()))
            ]
        if step + 1 < steps:
            state = (uniforms[:, None] > cumulative[state]).sum(axis=1)

    capital = returns.initial_capital
    levels = np.empty((n_paths, steps + 1), dtype=np.float64)
    levels[:, 0] = capital
    if returns.basis is Basis.FIXED_INITIAL:
        np.cumsum(drawn, axis=1, out=levels[:, 1:])
        levels[:, 1:] *= capital
        levels[:, 1:] += capital
    else:
        np.cumprod(1.0 + drawn, axis=1, out=levels[:, 1:])
        levels[:, 1:] *= capital

    return EquityPaths(
        values=np.ascontiguousarray(levels),
        unit=Unit.PERIOD,
        seed=seed,
        method=MARKOV_RESAMPLE + ("+collapsed" if transitions.collapsed else ""),
        period=returns.period,
    )
