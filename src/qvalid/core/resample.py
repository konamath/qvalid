"""Stationary bootstrap and the generation of simulated equity paths.

The observed backtest is one realisation of a stochastic process. Resampling it
under a scheme that preserves short range dependence produces the distribution
of results compatible with that history, which is the object every question in
``core/risk.py`` and ``core/propfirm.py`` is actually about.

Method: stationary bootstrap of Politis and Romano (1994). Blocks of random
geometric length, wrapped circularly, which keeps the resampled series
stationary rather than merely blockwise stationary. Expected block length
estimated automatically by Politis and White (2004), overridable.

Hypotheses. Weak stationarity and short range dependence. Under a structural
break the bootstrap mixes regimes and understates the tail, which is the reason
``core/regimes.py`` exists and why ``02`` section 2.2 adds a regime conditional
scheme in v0.5.

What this measures. The sampling variability of a statistic under a process
whose short range dependence matches the sample. What it does not measure.
Anything about a process the sample never visited: a regime absent from the
history cannot be resampled into existence, and the bootstrap says nothing
about it.

Verification of the block length estimator
------------------------------------------
Transcribing a formula from a paper proves nothing. The implementation here was
checked against the block length that actually minimises the mean squared error
of the estimator it is supposed to optimise. Under AR(1) with coefficient 0.5
and ``n = 1000``, where the true long run variance is ``1 / (1 - rho)^2 = 4``,
a brute force sweep of the exact stationary bootstrap variance estimator

    w(k) = ((n - |k|)/n) * (1 - 1/b)^|k| + (|k|/n) * (1 - 1/b)^(n - |k|)

over 120 replications puts the minimum at ``b = 10``. The plug in rule returns
10.56 on the same paths.

Two further properties were measured and are pinned by tests: the estimate is
monotone in the AR(1) coefficient, taking 1.39, 5.44, 10.86, 17.56 and 32.49 at
rho of 0, 0.2, 0.4, 0.6 and 0.8 with ``n = 2000``; and it scales as ``n^(1/3)``,
with observed ratios of 1.251, 1.307, 1.315 and 1.297 across successive
doublings of ``n``, against the 1.260 the rate predicts.

References
----------
Politis, D. N., and Romano, J. P. (1994). The stationary bootstrap. Journal of
the American Statistical Association 89(428), 1303-1313.

Politis, D. N., and White, H. (2004). Automatic block-length selection for the
dependent bootstrap. Econometric Reviews 23(1), 53-70.

Patton, A., Politis, D. N., and White, H. (2009). Correction to "Automatic
block-length selection for the dependent bootstrap". Econometric Reviews 28(4),
372-375.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from qvalid.contracts import (
    Basis,
    EquityPaths,
    FloatArray,
    IntArray,
    Period,
    PeriodReturns,
    TradeReturns,
    Unit,
)
from qvalid.core.constants import MIN_BLOCK_SAMPLE_RATIO
from qvalid.core.metrics import dispersion_is_negligible, inner_product
from qvalid.exceptions import InsufficientSampleError

__all__ = [
    "STATIONARY_BOOTSTRAP",
    "BlockLengthEstimate",
    "BootstrapResult",
    "estimate_block_length",
    "resample_equity_paths",
    "stationary_bootstrap",
    "stationary_bootstrap_indices",
]

STATIONARY_BOOTSTRAP = "stationary_bootstrap"
"""Method identifier stamped on :class:`~qvalid.contracts.EquityPaths`."""

_IID_BLOCK_LENGTH = 1.0
"""Expected block length at which the scheme degenerates to i.i.d. resampling."""


def _flat_top_weights(lags: IntArray, bandwidth: int) -> FloatArray:
    """Flat top lag window of Politis and White (2004).

    ``1`` on ``[0, 1/2]``, linearly down to zero on ``[1/2, 1]``, zero beyond.
    The flat section is what makes the resulting spectral estimate free of the
    first order bias a Bartlett window would introduce, which matters here
    because the quantity being estimated is itself a bias variance tradeoff.
    """
    scaled = np.abs(lags / bandwidth)
    return np.where(scaled <= 0.5, 1.0, np.where(scaled <= 1.0, 2.0 * (1.0 - scaled), 0.0))


@dataclass(frozen=True, slots=True)
class BlockLengthEstimate:
    """Expected block length, with every intermediate quantity that produced it.

    Attributes
    ----------
    block_length : float
        Expected block length ``b``. The scheme draws geometric lengths with
        success probability ``1 / b``.
    lag_selection : int
        ``m_hat``, the smallest lag past which the autocorrelations stop being
        individually significant.
    bandwidth : int
        ``M = min(2 * m_hat, M_max)``, the width of the flat top window.
    cap : float
        ``B_max = ceil(min(3 * sqrt(n), n / 3))``.
    capped : bool
        True when the raw estimate hit ``cap``. A capped estimate is a signal
        that the series is more persistent than the sample can resolve.
    n_obs : int
    automatic : bool
        False when the caller supplied ``block_length`` directly.
    warnings : tuple of str

    Notes
    -----
    Every field is carried into the report rather than logged, because the
    block length changes every number downstream and ``04`` forbids a
    statistical parameter that changes the result from being invisible.
    """

    block_length: float
    lag_selection: int
    bandwidth: int
    cap: float
    capped: bool
    n_obs: int
    automatic: bool
    warnings: tuple[str, ...]


def estimate_block_length(values: FloatArray) -> BlockLengthEstimate:
    """Estimate the expected block length by the plug in rule of Politis and White (2004).

    Parameters
    ----------
    values : numpy.ndarray of float64
        The series to be resampled. At least two observations.

    Returns
    -------
    BlockLengthEstimate

    Raises
    ------
    InsufficientSampleError
        Fewer than two observations, or an estimate above
        ``n / MIN_BLOCK_SAMPLE_RATIO``. ``02`` section 2.1 requires the second
        case to abort rather than proceed: above that ratio the resampled
        series is built from fewer than ten effectively independent blocks, so
        its coverage claim is void.

    Notes
    -----
    Procedure. With ``K = max(5, ceil(sqrt(log10 n)))`` and significance
    threshold ``2 * sqrt(log10(n) / n)``, take ``m_hat`` as the smallest ``m``
    whose following ``K`` autocorrelations are all below threshold, capped at
    ``M_max = ceil(sqrt(n)) + K``. Then with
    ``M = min(2 * m_hat, M_max, n - 1)`` and the flat top window ``lam``,

    ``g = sum_k lam(k/M) * R(k)``

    ``G = sum_k lam(k/M) * |k| * R(k)``

    ``b = (2 * G^2 / (2 * g^2))^(1/3) * n^(1/3)``

    truncated to ``[1, ceil(min(3 sqrt(n), n/3))]``. The constant ``2 * g^2`` is
    the stationary bootstrap case; the circular block bootstrap uses ``4/3``
    instead and is not implemented here.

    Degenerate cases. A series with no dispersion has no autocorrelation
    structure to estimate; it returns ``b = 1`` with a warning rather than
    dividing by zero. A series whose autocorrelations never fall below the
    threshold returns ``m_hat = M_max``, which is the honest answer that the
    sample cannot resolve the persistence, and it usually trips the ratio guard
    immediately afterwards.

    The bandwidth is additionally capped at ``n - 1``, the number of lags that
    exist. Without that cap a sample of two observations asks for lag 7 of a
    two element autocovariance vector. The cap is not cosmetic: it is the
    difference between a typed answer and an ``IndexError``, and the minimum
    sample case in the test suite is what exposed it.
    """
    n_obs = int(values.size)
    if n_obs < 2:
        raise InsufficientSampleError(
            "block length estimation needs at least two observations",
            observed=n_obs,
            threshold=2,
        )
    warnings: list[str] = []
    cap = float(math.ceil(min(3.0 * math.sqrt(n_obs), n_obs / 3.0)))

    if dispersion_is_negligible(values):
        return BlockLengthEstimate(
            block_length=_IID_BLOCK_LENGTH,
            lag_selection=0,
            bandwidth=0,
            cap=cap,
            capped=False,
            n_obs=n_obs,
            automatic=True,
            warnings=(
                "series has no dispersion at double precision, so there is no serial "
                "dependence to preserve; falling back to i.i.d. resampling with b = 1",
            ),
        )

    centred = np.ascontiguousarray(values - values.mean(), dtype=np.float64)
    lag_window = max(5, math.ceil(math.sqrt(math.log10(n_obs))))
    max_bandwidth = math.ceil(math.sqrt(n_obs)) + lag_window
    n_lags = min(max_bandwidth + lag_window, n_obs - 1)
    gamma = np.array(
        [inner_product(centred[k:], centred[: n_obs - k]) / n_obs for k in range(n_lags + 1)]
    )
    rho = gamma / gamma[0]

    threshold = 2.0 * math.sqrt(math.log10(n_obs) / n_obs)
    lag_selection = max_bandwidth
    inspected = False
    for candidate in range(1, min(max_bandwidth, n_lags) + 1):
        window = rho[candidate + 1 : candidate + lag_window + 1]
        if window.size == 0:
            continue
        inspected = True
        if bool(np.all(np.abs(window) < threshold)):
            lag_selection = candidate
            break
    else:
        warnings.append(
            f"autocorrelations never fall below {threshold:.4f} within {max_bandwidth} lags, "
            "so the sample cannot resolve the persistence of the series"
            if inspected
            else f"n = {n_obs} leaves fewer than {lag_window} lags to test for significance, "
            "so the lag selection step is vacuous and the block length is a formality"
        )

    bandwidth = int(min(2 * lag_selection, max_bandwidth, n_lags))
    # bandwidth is at least 1: lag_selection is at least 1 and n_lags is at
    # least 1 for any sample of two or more, so no zero width branch is needed.
    lags = np.arange(-bandwidth, bandwidth + 1)
    weights = _flat_top_weights(lags, bandwidth)
    covariances = gamma[np.abs(lags)]
    g_hat = float((weights * covariances).sum())
    g_derivative = float((weights * np.abs(lags) * covariances).sum())

    if g_hat == 0.0 or g_derivative == 0.0:
        raw = _IID_BLOCK_LENGTH
        warnings.append(
            "the flat top spectral estimate is degenerate, which happens when the "
            "autocovariances cancel; falling back to i.i.d. resampling with b = 1"
        )
    else:
        raw = ((2.0 * g_derivative**2) / (2.0 * g_hat**2)) ** (1.0 / 3.0) * n_obs ** (1.0 / 3.0)

    block_length = float(min(max(raw, _IID_BLOCK_LENGTH), cap))
    capped = raw > cap
    if capped:
        warnings.append(
            f"raw block length {raw:.2f} exceeds the cap {cap:.0f}; the series is more "
            "persistent than a sample of this length can resolve"
        )

    ratio_limit = n_obs / MIN_BLOCK_SAMPLE_RATIO
    if block_length > ratio_limit:
        raise InsufficientSampleError(
            f"estimated block length leaves fewer than {MIN_BLOCK_SAMPLE_RATIO:.0f} effectively "
            "independent blocks, so the bootstrap distribution would be driven by a handful of "
            "resampled segments and its coverage claim would be void; collect more data or "
            "coarsen the grid",
            observed=block_length,
            threshold=ratio_limit,
        )

    return BlockLengthEstimate(
        block_length=block_length,
        lag_selection=lag_selection,
        bandwidth=bandwidth,
        cap=cap,
        capped=capped,
        n_obs=n_obs,
        automatic=True,
        warnings=tuple(warnings),
    )


def stationary_bootstrap_indices(
    n_obs: int,
    *,
    n_paths: int,
    n_steps: int,
    block_length: float,
    seed: int,
) -> IntArray:
    """Draw the resampling index matrix of the stationary bootstrap.

    Parameters
    ----------
    n_obs : int
        Length of the source series.
    n_paths, n_steps : int
        Shape of the output, both strictly positive.
    block_length : float
        Expected block length ``b``, at least 1. Blocks have geometric length
        with success probability ``1 / b``, so ``b = 1`` makes every step start
        a new block, which is exactly i.i.d. resampling with replacement.
    seed : int
        Mandatory. ``numpy.random.default_rng(seed)`` is constructed inside the
        function and no global random state is touched, per ``04``.

    Returns
    -------
    numpy.ndarray of int64, shape ``(n_paths, n_steps)``
        Positions into the source series, wrapped circularly.

    Notes
    -----
    Fully vectorised. The textbook form of the scheme is a recursion in ``t``,
    which would be ``n_steps`` Python iterations. It is replaced here by an
    accumulation of maxima:

    ``last = maximum.accumulate(where(new_block, t, -1))``

    gives, for every position, the index of the most recent block start, and
    the offset within the block is ``t - last``. Column zero is forced to start
    a block, so ``last`` is never negative and no special case is needed.

    Circular wrapping is part of the method, not a convenience. It is what
    makes every observation equally likely to appear, which in turn is what
    makes the resampled series stationary; a non wrapping variant underweights
    the tail of the sample.

    Cost, measured rather than asserted, per the performance rule of ``04``.
    Several ``(n_paths, n_steps)`` arrays exist simultaneously, so the index
    matrix alone is ``8 * n_paths * n_steps`` bytes and peak allocation is a
    few multiples of that. Timings for the index draw:

    ======  ======  ========  ==========
    paths   steps   seconds   index size
    ======  ======  ========  ==========
    10000   2000    0.51      160 MB
    10000   600     0.12      48 MB
    1000    2000    0.04      16 MB
    ======  ======  ========  ==========

    At the suggested default of 10000 replications and a three year daily grid,
    that is well under a second, so step 1 of the performance ladder in ``04``,
    vectorise in NumPy, is where this stops. Chunking over paths is the obvious
    next step if the ceiling ever binds, and per ``04`` it does not enter
    without a measurement showing it is needed.
    """
    if n_obs < 1:
        raise InsufficientSampleError(
            "resampling needs at least one observation", observed=n_obs, threshold=1
        )
    if n_paths < 1 or n_steps < 1:
        raise InsufficientSampleError(
            "n_paths and n_steps must both be strictly positive",
            observed=(n_paths, n_steps),
            threshold=(1, 1),
        )
    if block_length < _IID_BLOCK_LENGTH:
        raise InsufficientSampleError(
            "expected block length must be at least 1",
            observed=block_length,
            threshold=_IID_BLOCK_LENGTH,
        )

    rng = np.random.default_rng(seed)
    restart_probability = 1.0 / block_length
    anchors = rng.integers(0, n_obs, size=(n_paths, n_steps), dtype=np.int64)
    new_block = rng.random((n_paths, n_steps)) < restart_probability
    new_block[:, 0] = True

    step = np.arange(n_steps, dtype=np.int64)
    last_start = np.maximum.accumulate(np.where(new_block, step, -1), axis=1)
    offset = step - last_start
    anchor = np.take_along_axis(anchors, last_start, axis=1)
    return np.ascontiguousarray((anchor + offset) % n_obs, dtype=np.int64)


def stationary_bootstrap(
    values: FloatArray,
    *,
    n_paths: int,
    seed: int,
    block_length: float | None = None,
    n_steps: int | None = None,
) -> tuple[FloatArray, BlockLengthEstimate]:
    """Resample a series into a matrix of paths, preserving short range dependence.

    Parameters
    ----------
    values : numpy.ndarray of float64
    n_paths : int
    seed : int
        Mandatory and explicit.
    block_length : float or None, optional
        ``None`` estimates it by :func:`estimate_block_length`. A supplied
        value overrides the estimate and is reported as non automatic.
    n_steps : int or None, optional
        Path length. ``None`` uses the length of the source series, which is
        the right default for a distribution of the observed statistic. A
        longer horizon is meaningful for forward looking questions and is what
        ``core/risk.py`` uses for risk of ruin over a declared horizon.

    Returns
    -------
    tuple of (numpy.ndarray, BlockLengthEstimate)
        The ``(n_paths, n_steps)`` matrix of resampled values, and the block
        length provenance.

    Notes
    -----
    Passing ``block_length=1`` gives i.i.d. resampling through the same code
    path, which is why no separate i.i.d. bootstrap function exists. The
    comparison required by ``02`` section 2.1, that block resampling preserves
    first order autocorrelation better than i.i.d. resampling, then measures a
    difference of method rather than a difference of implementation.
    """
    if block_length is None:
        estimate = estimate_block_length(values)
    else:
        estimate = BlockLengthEstimate(
            block_length=float(block_length),
            lag_selection=0,
            bandwidth=0,
            cap=float("inf"),
            capped=False,
            n_obs=int(values.size),
            automatic=False,
            warnings=(
                f"block length {block_length:g} was supplied, overriding automatic selection",
            ),
        )
    steps = int(values.size) if n_steps is None else int(n_steps)
    index = stationary_bootstrap_indices(
        int(values.size),
        n_paths=n_paths,
        n_steps=steps,
        block_length=estimate.block_length,
        seed=seed,
    )
    return np.ascontiguousarray(values[index], dtype=np.float64), estimate


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Simulated paths plus everything needed to reproduce and interpret them.

    Attributes
    ----------
    paths : EquityPaths
        Absolute equity levels, shape ``(n_paths, n_steps + 1)``, column zero
        equal to the initial capital.
    block_length : BlockLengthEstimate
    basis : Basis
    initial_capital : float
    warnings : tuple of str

    Notes
    -----
    The block length is carried here rather than added to
    :class:`~qvalid.contracts.EquityPaths`, following the pattern already used by
    ``GridSelection`` and ``ImportResult``: the contract stays as ``01``
    defines it, and the provenance the report needs travels alongside it. The
    basis and initial capital are recoverable from the paths themselves, since
    the values are absolute levels, but they are restated so the report does
    not have to infer them.
    """

    paths: EquityPaths
    block_length: BlockLengthEstimate
    basis: Basis
    initial_capital: float
    warnings: tuple[str, ...]

    @property
    def unit(self) -> Unit:
        """Index unit propagated from the resampled series."""
        return self.paths.unit


def resample_equity_paths(
    returns: TradeReturns | PeriodReturns,
    *,
    n_paths: int,
    seed: int,
    block_length: float | None = None,
    n_steps: int | None = None,
) -> BootstrapResult:
    """Resample a return series into simulated equity paths.

    Parameters
    ----------
    returns : TradeReturns or PeriodReturns
        The ``unit`` of the input propagates to the output and determines what
        may consume the paths. See ``01``.
    n_paths : int
    seed : int
    block_length : float or None, optional
    n_steps : int or None, optional

    Returns
    -------
    BootstrapResult

    Raises
    ------
    InsufficientSampleError
        Fewer than two observations, or a block length above the ratio guard.
    UnitMismatchError
        Raised by the :class:`~qvalid.contracts.EquityPaths` constructor when
        unit and period disagree. This function never chooses the unit: it
        reads it off the input contract, so a trade indexed series can never
        produce a path that a daily loss limit would accept.

    Notes
    -----
    Paths are absolute equity levels rather than returns, because drawdown and
    absorbing barriers are defined on levels and ``core/risk.py`` needs both.
    The level is built by the same rule as
    :func:`~qvalid.core.metrics.equity_curve`: additive on the initial capital
    under ``FIXED_INITIAL``, multiplicative on running equity under
    ``CURRENT_EQUITY``. Using one rule for the observed path and another for
    the simulated ones would make the observed drawdown incomparable with the
    distribution it is supposed to be a quantile of.

    Under ``CURRENT_EQUITY`` a resampled path can in principle compound to a
    non positive level, since resampling can concatenate losses the history
    never showed consecutively. That is not an error: it is ruin, and it is the
    event ``core/risk.py`` is built to measure. The path is kept as is and the
    count of ruined paths is reported.
    """
    values = np.asarray(returns.values, dtype=np.float64)
    resampled, estimate = stationary_bootstrap(
        values, n_paths=n_paths, seed=seed, block_length=block_length, n_steps=n_steps
    )
    capital = returns.initial_capital
    warnings = list(estimate.warnings)

    levels = np.empty((resampled.shape[0], resampled.shape[1] + 1), dtype=np.float64)
    levels[:, 0] = capital
    if returns.basis is Basis.FIXED_INITIAL:
        np.cumsum(resampled, axis=1, out=levels[:, 1:])
        levels[:, 1:] *= capital
        levels[:, 1:] += capital
    else:
        np.cumprod(1.0 + resampled, axis=1, out=levels[:, 1:])
        levels[:, 1:] *= capital
        ruined = int((levels[:, 1:] <= 0.0).any(axis=1).sum())
        if ruined:
            warnings.append(
                f"{ruined} of {n_paths} paths reach non positive equity under "
                "basis=CURRENT_EQUITY; that is ruin, not an error, and core/risk.py "
                "measures its probability"
            )

    period: Period | None = returns.period if isinstance(returns, PeriodReturns) else None
    paths = EquityPaths(
        values=levels,
        unit=returns.unit,
        seed=seed,
        method=STATIONARY_BOOTSTRAP,
        period=period,
    )
    return BootstrapResult(
        paths=paths,
        block_length=estimate,
        basis=returns.basis,
        initial_capital=capital,
        warnings=tuple(warnings),
    )
