"""Projection of a ``TradeLog`` onto the two return series of D006.

Two series, two roles, no conversion path between them:

``TradeReturns``
    Indexed by trade number. Feeds expectancy, hit rate, profit factor. Never
    annualised, and structurally incapable of being annualised because the
    contract carries no ``periods_per_year``.

``PeriodReturns``
    Indexed by calendar period. The only admissible source of any annualised
    number. Built by attributing each trade's P&L to the period containing its
    ``exit_ts``, per ``02`` section 1.1 and D006 part 2.

The grid is chosen by the ladder rule of ``02`` section 1.1: the finest step of
DAILY, WEEKLY, MONTHLY that satisfies all three feasibility conditions. Finest,
because the standard error of the Sharpe ratio scales with the inverse square
root of the sample size, so among feasible grids the finest maximises T. An
empty feasible set raises :class:`~qvalid.exceptions.GridSparsityError` rather
than returning a Sharpe ratio that measures period count.

Implementation decisions recorded here rather than in a comment
----------------------------------------------------------------
**Grid extent.** The grid spans the first period holding a trade to the last,
not the extent of the supplied calendar. A calendar materialised over ten years
for a log covering one would otherwise drive ``active_fraction`` and
``n_periods`` by an arbitrary choice made in the adapter layer. Trimming makes
every reported quantity a function of the log alone. The cost is that the first
and last grid periods are active by construction, which lifts
``active_fraction`` on very short samples; the effect is bounded by
``2 / n_periods`` and vanishes well before ``MIN_PERIODS``.

**Where ``periods_per_year`` comes from.** For a daily grid, from
:meth:`~qvalid.contracts.TradingCalendar.sessions_per_year`, that is the observed
session rate of the calendar over its full span, not over the trimmed grid. The
rate is a property of the venue, and deriving it from the trimmed grid would
make it a function of when the strategy happened to trade. For weekly and
monthly grids the rate is a property of the Gregorian calendar rather than of
any exchange, 365.25 / 7 and 12 exactly, so nothing is estimated. A holiday
week is still a week of allocated capital.

**Holding duration condition.** Measured as median holding duration in
nanoseconds against the median grid period length in nanoseconds, and compared
to ``MAX_HOLDING_TO_PERIOD`` as the ratio the constant is declared to be. The
alternative, counting how many grid periods each holding interval spans, was
rejected: an overnight trade of seventeen hours spans two daily periods but
lasts less than one, so the span form would push most intraday-to-open
strategies off the daily grid for a reason that is an artefact of bucket
boundaries. The nanosecond form also stays defined when ``entry_ts`` precedes
the calendar, which happens for a position carried into the sample.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from qvalid.contracts import (
    NANOS_PER_SECOND,
    Basis,
    FloatArray,
    IntArray,
    Period,
    PeriodReturns,
    TradeLog,
    TradeReturns,
    TradingCalendar,
)
from qvalid.core.constants import (
    MAX_HOLDING_TO_PERIOD,
    MIN_ACTIVE_FRACTION,
    MIN_PERIODS,
    MONTHS_PER_YEAR,
    WEEKS_PER_YEAR,
)
from qvalid.exceptions import (
    CalendarCoverageError,
    GridSparsityError,
    InsufficientSampleError,
    TradeIntegrityError,
)

__all__ = [
    "LADDER",
    "GridCandidate",
    "GridSelection",
    "period_returns",
    "running_equity",
    "select_grid",
    "trade_returns",
]

LADDER: tuple[Period, ...] = (Period.DAILY, Period.WEEKLY, Period.MONTHLY)
"""Ordered grid ladder, finest first. The engine never selects outside it."""

_NANOS_PER_DAY = 86_400 * NANOS_PER_SECOND
_NANOS_PER_WEEK = 7 * _NANOS_PER_DAY
_MONDAY_EPOCH_DAY_RESIDUE = 4
"""1970-01-01 was a Thursday, so epoch days that are Mondays satisfy ``day % 7 == 4``."""

_NOMINAL_PERIOD_NS: dict[Period, int] = {
    Period.DAILY: _NANOS_PER_DAY,
    Period.WEEKLY: _NANOS_PER_WEEK,
    Period.MONTHLY: int(365.25 / 12 * _NANOS_PER_DAY),
}
"""Fallback period length, used only when the grid holds a single period."""


@dataclass(frozen=True, slots=True)
class GridCandidate:
    """Feasibility diagnosis of one rung of the ladder.

    Attributes
    ----------
    period : Period
    n_periods : int
    active_fraction : float
    holding_ratio : float
        Median holding duration over median period length.
    feasible : bool
    rejections : tuple of str
        Human readable reason per violated condition, each carrying the
        observed value and the threshold. Empty when ``feasible``.

    Notes
    -----
    Every rung is diagnosed, including the ones after the selected grid, so the
    report can show why a coarser grid was not needed and why a finer one was
    refused. Reporting only the winner would hide the fact that the choice was
    marginal.
    """

    period: Period
    n_periods: int
    active_fraction: float
    holding_ratio: float
    feasible: bool
    rejections: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GridSelection:
    """Outcome of the ladder rule, with the full diagnosis attached.

    Attributes
    ----------
    returns : PeriodReturns
    candidates : tuple of GridCandidate
        One entry per rung of :data:`LADDER`, in ladder order.
    forced : bool
        True when the user pinned the grid instead of letting the rule choose.
    warnings : tuple of str
        Non empty only when ``forced`` and some condition is violated. Under a
        forced grid the three conditions degrade from error to warning, per
        ``02`` section 1.1, and the warnings travel into the report.
    """

    returns: PeriodReturns
    candidates: tuple[GridCandidate, ...]
    forced: bool
    warnings: tuple[str, ...]

    @property
    def period(self) -> Period:
        """Grid step actually used."""
        return self.returns.period


def running_equity(log: TradeLog, initial_capital: float) -> FloatArray:
    """Account equity before each trade, plus the final equity.

    Parameters
    ----------
    log : TradeLog
        Assumed validated and ordered by ``exit_ns``, per ``04``.
    initial_capital : float
        Strictly positive.

    Returns
    -------
    numpy.ndarray of float64
        Length ``n_trades + 1``. Element ``i`` is the equity before trade ``i``
        and the last element is the equity after the final trade.

    Notes
    -----
    Order matters and is a contract guarantee, not something re-established
    here. ``core`` does not revalidate.
    """
    equity = np.empty(log.n_trades + 1, dtype=np.float64)
    equity[0] = initial_capital
    np.cumsum(log.pnl, out=equity[1:])
    equity[1:] += initial_capital
    return equity


def _require_non_empty(log: TradeLog) -> None:
    if log.n_trades == 0:
        raise InsufficientSampleError(
            "cannot project an empty TradeLog onto a return series",
            observed=0,
            threshold=1,
        )


def _require_positive_capital(initial_capital: float) -> None:
    if initial_capital <= 0.0:
        raise TradeIntegrityError(
            "initial_capital must be strictly positive",
            observed=initial_capital,
            threshold=0.0,
        )


def _check_solvency(equity: FloatArray, basis: Basis) -> None:
    """Refuse a percentage basis over an account that reached zero.

    Under ``CURRENT_EQUITY`` the denominator is the running equity, so a non
    positive value makes the percentage return undefined, per ``01``. The
    error names the remedy, because the run is not hopeless: a blown account
    is perfectly representable under ``FIXED_INITIAL``, where the cumulative
    return simply reaches minus one hundred per cent or worse.
    """
    if basis is not Basis.CURRENT_EQUITY:
        return
    bad = equity <= 0.0
    if not bool(bad.any()):
        return
    first = int(np.argmax(bad))
    raise TradeIntegrityError(
        "equity reached zero or below under basis=CURRENT_EQUITY, where the running "
        f"equity is the denominator; first at trade index {first - 1 if first else 0} "
        "of the log. Re-run under basis=FIXED_INITIAL, which represents ruin as a "
        "cumulative return of minus one hundred per cent or worse",
        observed=float(equity[bad].min()),
        threshold=0.0,
    )


def trade_returns(
    log: TradeLog,
    *,
    basis: Basis,
    initial_capital: float,
) -> TradeReturns:
    """Project a trade log onto returns indexed by trade number.

    Parameters
    ----------
    log : TradeLog
        Validated log, ordered by ``exit_ns``.
    basis : Basis
        ``FIXED_INITIAL`` divides every P&L by ``initial_capital``, giving
        additive returns. ``CURRENT_EQUITY`` divides trade ``i`` by the equity
        standing before it, giving multiplicatively composable returns.
    initial_capital : float
        Strictly positive.

    Returns
    -------
    TradeReturns

    Raises
    ------
    InsufficientSampleError
        Empty log.
    TradeIntegrityError
        Non positive ``initial_capital``, or running equity reaching zero under
        ``CURRENT_EQUITY``.

    Notes
    -----
    The index is trade number, so nothing computed from this object may be
    annualised. The prohibition is structural: :class:`TradeReturns` carries no
    ``periods_per_year``, so an annualisation function cannot take it. See D006.

    Under ``CURRENT_EQUITY`` this series is the stricter of the two: it divides
    by the equity before every single trade, whereas ``PeriodReturns`` divides
    by the equity at the start of each period. An account that dips below zero
    mid period and recovers within it is refused here and would have been
    silently representable there. Applying the solvency check to the running
    equity, not to the period boundaries, keeps the two series from disagreeing
    about whether the account survived.
    """
    _require_non_empty(log)
    _require_positive_capital(initial_capital)
    equity = running_equity(log, initial_capital)
    _check_solvency(equity, basis)
    values = log.pnl / initial_capital if basis is Basis.FIXED_INITIAL else log.pnl / equity[:-1]
    return TradeReturns(
        values=np.ascontiguousarray(values, dtype=np.float64),
        basis=basis,
        initial_capital=initial_capital,
    )


def _check_calendar_coverage(log: TradeLog, calendar: TradingCalendar) -> None:
    """Refuse a calendar that does not span the exit timestamps of the log."""
    closes = calendar.session_close_ns
    last_exit = int(log.exit_ns[-1])
    if last_exit > int(closes[-1]):
        raise CalendarCoverageError(
            f"calendar {calendar.calendar_id!r} ends before the log does; "
            f"{int((log.exit_ns > closes[-1]).sum())} trades exit after the last session",
            observed=np.datetime64(last_exit, "ns"),
            threshold=np.datetime64(int(closes[-1]), "ns"),
        )
    gap = int(np.median(np.diff(closes))) if closes.size > 1 else _NANOS_PER_DAY
    earliest_attributable = int(closes[0]) - gap
    first_exit = int(log.exit_ns[0])
    if first_exit <= earliest_attributable:
        raise CalendarCoverageError(
            f"calendar {calendar.calendar_id!r} starts after the log does; "
            "the earliest trades have no session to be attributed to",
            observed=np.datetime64(first_exit, "ns"),
            threshold=np.datetime64(earliest_attributable, "ns"),
        )


def _grid_edges(log: TradeLog, calendar: TradingCalendar, period: Period) -> IntArray:
    """Build the closing instants of the grid periods spanning the log, inclusive.

    Element ``k`` is the last instant belonging to period ``k``, so assignment
    is a single ``searchsorted(edges, exit_ns, side="left")`` for every rung of
    the ladder. For a daily grid the edges are the session closes themselves.
    For weekly and monthly grids they are civil calendar boundaries minus one
    nanosecond, which keeps the same one sided convention.
    """
    first = int(log.exit_ns[0])
    last = int(log.exit_ns[-1])
    if period is Period.DAILY:
        closes = calendar.session_close_ns
        lo = int(np.searchsorted(closes, first, side="left"))
        hi = int(np.searchsorted(closes, last, side="left"))
        return np.ascontiguousarray(closes[lo : hi + 1], dtype=np.int64)
    if period is Period.WEEKLY:
        first_day = first // _NANOS_PER_DAY
        last_day = last // _NANOS_PER_DAY
        first_monday = first_day - (first_day - _MONDAY_EPOCH_DAY_RESIDUE) % 7
        last_monday = last_day - (last_day - _MONDAY_EPOCH_DAY_RESIDUE) % 7
        starts = np.arange(first_monday, last_monday + 8, 7, dtype=np.int64) * _NANOS_PER_DAY
        return np.ascontiguousarray(starts[1:] - 1, dtype=np.int64)
    first_month = np.datetime64(first, "ns").astype("datetime64[M]").astype(np.int64)
    last_month = np.datetime64(last, "ns").astype("datetime64[M]").astype(np.int64)
    months = np.arange(first_month, last_month + 2, dtype=np.int64)
    starts = months.astype("datetime64[M]").astype("datetime64[ns]").astype(np.int64)
    return np.ascontiguousarray(starts[1:] - 1, dtype=np.int64)


def _periods_per_year(calendar: TradingCalendar, period: Period) -> float:
    if period is Period.DAILY:
        return calendar.sessions_per_year()
    if period is Period.WEEKLY:
        return WEEKS_PER_YEAR
    return MONTHS_PER_YEAR


def _median_period_ns(edges: IntArray, period: Period) -> float:
    if edges.size > 1:
        return float(np.median(np.diff(edges)))
    return float(_NOMINAL_PERIOD_NS[period])


def period_returns(
    log: TradeLog,
    calendar: TradingCalendar,
    *,
    period: Period,
    basis: Basis,
    initial_capital: float,
) -> PeriodReturns:
    """Project a trade log onto returns indexed by calendar period.

    Parameters
    ----------
    log : TradeLog
        Validated log, ordered by ``exit_ns``.
    calendar : TradingCalendar
        Supplied as a typed argument, never fetched. See ``01``.
    period : Period
        Grid step. Use :func:`select_grid` to have the ladder rule choose it.
    basis : Basis
    initial_capital : float

    Returns
    -------
    PeriodReturns
        Spanning the first period holding a trade to the last, inclusive, with
        empty periods carried as zeros.

    Raises
    ------
    InsufficientSampleError
        Empty log.
    CalendarCoverageError
        The calendar does not span the exit timestamps of the log.
    TradeIntegrityError
        Non positive ``initial_capital``, or running equity reaching zero under
        ``CURRENT_EQUITY``.

    Notes
    -----
    Attribution is to the period containing ``exit_ts``, per ``02`` section 1.1
    and D006 part 2. Distributing P&L across the holding interval by mark to
    market would require a price series inside a descriptive metric and is out
    of scope in every version, because it would break the offline guarantee of
    D003.

    Under ``FIXED_INITIAL`` the period return is the arithmetic sum of the
    attributed P&L over the initial capital. Under ``CURRENT_EQUITY`` it is the
    attributed P&L over the equity standing at the start of the period, which
    is algebraically identical to compounding the individual trade returns
    within the period, since ``equity_end / equity_start - 1`` equals
    ``sum(pnl) / equity_start``. The two readings of the composition rule in
    ``01`` therefore coincide and no choice is being hidden here.

    ``n_active`` counts periods holding at least one attributed trade, not
    periods with a non zero return. A scratch trade produces exactly zero P&L
    in a period that was genuinely active, and conflating the two would
    understate the active fraction and trip the sparsity guard for the wrong
    reason.
    """
    _require_non_empty(log)
    _require_positive_capital(initial_capital)
    _check_calendar_coverage(log, calendar)

    equity = running_equity(log, initial_capital)
    _check_solvency(equity, basis)

    edges = _grid_edges(log, calendar, period)
    index = np.searchsorted(edges, log.exit_ns, side="left")
    n_periods = int(edges.size)

    pnl_by_period = np.bincount(index, weights=log.pnl, minlength=n_periods).astype(np.float64)
    trades_by_period = np.bincount(index, minlength=n_periods)

    if basis is Basis.FIXED_INITIAL:
        values = pnl_by_period / initial_capital
    else:
        equity_end = initial_capital + np.cumsum(pnl_by_period)
        equity_start = np.empty_like(equity_end)
        equity_start[0] = initial_capital
        equity_start[1:] = equity_end[:-1]
        values = pnl_by_period / equity_start

    return PeriodReturns(
        values=np.ascontiguousarray(values, dtype=np.float64),
        period_end_ns=edges,
        period=period,
        periods_per_year=_periods_per_year(calendar, period),
        calendar_id=calendar.calendar_id,
        basis=basis,
        initial_capital=initial_capital,
        n_active=int((trades_by_period > 0).sum()),
    )


def _diagnose(returns: PeriodReturns, edges: IntArray, median_holding_ns: float) -> GridCandidate:
    """Evaluate the three feasibility conditions of ``02`` section 1.1."""
    period_ns = _median_period_ns(edges, returns.period)
    holding_ratio = median_holding_ns / period_ns
    rejections: list[str] = []
    if returns.active_fraction < MIN_ACTIVE_FRACTION:
        rejections.append(
            f"active_fraction={returns.active_fraction:.4f} below "
            f"MIN_ACTIVE_FRACTION={MIN_ACTIVE_FRACTION}"
        )
    if returns.n_periods < MIN_PERIODS:
        rejections.append(f"n_periods={returns.n_periods} below MIN_PERIODS={MIN_PERIODS}")
    if holding_ratio > MAX_HOLDING_TO_PERIOD:
        rejections.append(
            f"median holding over median period length={holding_ratio:.4f} above "
            f"MAX_HOLDING_TO_PERIOD={MAX_HOLDING_TO_PERIOD}"
        )
    return GridCandidate(
        period=returns.period,
        n_periods=returns.n_periods,
        active_fraction=returns.active_fraction,
        holding_ratio=holding_ratio,
        feasible=not rejections,
        rejections=tuple(rejections),
    )


def select_grid(
    log: TradeLog,
    calendar: TradingCalendar,
    *,
    basis: Basis,
    initial_capital: float,
    forced_period: Period | None = None,
) -> GridSelection:
    """Choose the grid by the ladder rule of ``02`` section 1.1.

    Parameters
    ----------
    log : TradeLog
    calendar : TradingCalendar
    basis : Basis
    initial_capital : float
    forced_period : Period or None, optional
        When given, that grid is used regardless of feasibility and the three
        conditions degrade from error to warning. The warnings travel into the
        ``ValidationReport``, so a forced grid is declared, never silent.

    Returns
    -------
    GridSelection
        Holding the chosen :class:`~qvalid.contracts.PeriodReturns` and the
        diagnosis of every rung, feasible or not.

    Raises
    ------
    GridSparsityError
        No rung satisfies all three conditions and none was forced. The message
        lists, for every rung, which conditions failed and by how much.

    Notes
    -----
    The finest feasible rung wins because the standard error of the Sharpe
    ratio scales as one over the square root of the sample size, so among grids
    that are all admissible the finest carries the most information. The engine
    never selects outside :data:`LADDER` and never infers the calendar.

    Coarsening is not monotone in every condition, which is why all three rungs
    are evaluated instead of stopping at the first failure. Moving from daily
    to weekly raises ``active_fraction`` and lowers ``holding_ratio``, both
    helpful, but also cuts ``n_periods`` by roughly five, which can break the
    ``MIN_PERIODS`` condition that daily satisfied. A sample can therefore be
    infeasible on every rung for different reasons on each, and the error
    message has to say so.
    """
    _require_non_empty(log)
    median_holding_ns = float(np.median((log.exit_ns - log.entry_ns).astype(np.float64)))

    candidates: list[GridCandidate] = []
    projections: dict[Period, PeriodReturns] = {}
    for rung in LADDER:
        projected = period_returns(
            log, calendar, period=rung, basis=basis, initial_capital=initial_capital
        )
        projections[rung] = projected
        candidates.append(_diagnose(projected, projected.period_end_ns, median_holding_ns))

    if forced_period is not None:
        forced_candidate = next(c for c in candidates if c.period is forced_period)
        return GridSelection(
            returns=projections[forced_period],
            candidates=tuple(candidates),
            forced=True,
            warnings=tuple(
                f"forced grid {forced_period}: {reason}" for reason in forced_candidate.rejections
            ),
        )

    for candidate in candidates:
        if candidate.feasible:
            return GridSelection(
                returns=projections[candidate.period],
                candidates=tuple(candidates),
                forced=False,
                warnings=(),
            )

    detail = " | ".join(f"{c.period}: {'; '.join(c.rejections)}" for c in candidates)
    raise GridSparsityError(
        "no grid on the ladder satisfies the three conditions of 02 section 1.1; "
        "reporting an annualised statistic here would measure period count, not performance",
        observed=tuple(str(c.period) for c in candidates),
        threshold=(
            f"active_fraction >= {MIN_ACTIVE_FRACTION}, n_periods >= {MIN_PERIODS}, "
            f"holding ratio <= {MAX_HOLDING_TO_PERIOD}"
        ),
        detail=detail,
    )
