"""Canonical data contracts.

Design rules enforced here, from ``01_escopo_e_arquitetura.md`` and D006:

1. Columnar storage. Every record collection is a frozen dataclass holding
   parallel one dimensional NumPy arrays, not a sequence of row objects. The
   P&L coherence identity and the grid projection are vector operations, and
   row objects would force a Python level loop over them.
2. Timestamps are stored as ``int64`` nanoseconds since the Unix epoch in UTC.
   ``numpy.datetime64`` carries no timezone, so a naive array would silently
   satisfy a "tz aware" requirement it cannot express. Conversion happens in
   :func:`to_utc_nanos`, which rejects naive input.
3. Ownership transfer. Constructing a contract marks the supplied arrays read
   only in place. No copy is made, so the caller must not reuse the arrays for
   mutation afterwards.
4. Structural invariants only. ``__post_init__`` checks shape and dtype and
   raises :class:`~qvalid.exceptions.SchemaError`. Value level invariants, such
   as P&L coherence, live at the adapter boundary and raise
   :class:`~qvalid.exceptions.TradeIntegrityError`. See D007 and ``04``.

The separation between :class:`TradeReturns` and :class:`PeriodReturns` is the
structural form of D006: no annualisation function accepts ``TradeReturns``,
because ``TradeReturns`` does not carry ``periods_per_year`` at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt

from qvalid.exceptions import SchemaError, UnitMismatchError

__all__ = [
    "UNDEFINED_STATE",
    "Basis",
    "EquityPaths",
    "Period",
    "PeriodReturns",
    "RegimeLabels",
    "Side",
    "TradeLog",
    "TradeReturns",
    "TradingCalendar",
    "TrialMatrix",
    "Unit",
    "to_utc_nanos",
]

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]
SideArray = npt.NDArray[np.int8]
"""Side is plus or minus one, so int8 is the honest width and eight times smaller."""

NANOS_PER_SECOND = 1_000_000_000


class Side(IntEnum):
    """Trade direction, valued so that it multiplies directly into the P&L identity.

    ``LONG`` is ``+1`` and ``SHORT`` is ``-1``. The arithmetic meaning is the
    definition, not an encoding detail: the gross P&L identity of ``01`` reads

    ``pnl_gross = side * (exit_px - entry_px) * qty * multiplier``

    so an :class:`enum.IntEnum` removes a branch from the vectorised check.
    """

    LONG = 1
    SHORT = -1


class Basis(StrEnum):
    """Denominator convention for a return series.

    ``FIXED_INITIAL`` divides P&L by the initial capital, giving additive
    period returns. ``CURRENT_EQUITY`` divides by the running equity, giving
    multiplicatively composable returns. The choice changes every downstream
    number and is therefore a mandatory contract field, printed in the report.
    """

    FIXED_INITIAL = "FIXED_INITIAL"
    CURRENT_EQUITY = "CURRENT_EQUITY"


class Period(StrEnum):
    """Calendar grid step. Ordered ladder used by the grid selection rule."""

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class Unit(StrEnum):
    """Index unit of a simulated path.

    ``TRADE`` paths are indexed by trade number and carry no calendar meaning.
    ``PERIOD`` paths are indexed by calendar period. Daily loss limits and
    declared horizons require ``PERIOD``.
    """

    TRADE = "TRADE"
    PERIOD = "PERIOD"


def to_utc_nanos(stamps: Sequence[datetime]) -> IntArray:
    """Convert timezone aware datetimes to int64 UTC nanoseconds.

    Parameters
    ----------
    stamps : sequence of datetime.datetime
        Every element must carry ``tzinfo`` with a defined UTC offset.

    Returns
    -------
    numpy.ndarray of int64
        Nanoseconds since the Unix epoch, in UTC.

    Raises
    ------
    SchemaError
        If any element is naive. ``01`` forbids naive timestamps at every
        boundary, and a naive value here would be silently reinterpreted as
        local time by the operating system.

    Notes
    -----
    Timezone conversion happens before truncation, so an input expressed in a
    non UTC zone is converted rather than rejected. Only the absence of zone
    information is an error.
    """
    out = np.empty(len(stamps), dtype=np.int64)
    for i, stamp in enumerate(stamps):
        if stamp.tzinfo is None or stamp.tzinfo.utcoffset(stamp) is None:
            raise SchemaError(
                f"naive timestamp at position {i}: {stamp!r}. "
                "All timestamps must be timezone aware, see 01."
            )
        seconds = stamp.astimezone(UTC).timestamp()
        out[i] = round(seconds * NANOS_PER_SECOND)
    return out


def _freeze(array: npt.NDArray[Any], name: str, dtype: Any, ndim: int = 1) -> npt.NDArray[Any]:
    """Check dtype and dimensionality, then mark the array read only in place."""
    if not isinstance(array, np.ndarray):
        raise SchemaError(f"{name} must be a numpy array, got {type(array).__name__}")
    if array.ndim != ndim:
        raise SchemaError(f"{name} must be {ndim}-dimensional, got ndim={array.ndim}")
    if dtype is not None and array.dtype != dtype:
        raise SchemaError(f"{name} must have dtype {dtype}, got {array.dtype}")
    array.flags.writeable = False
    return array


def _require_same_length(fields: Mapping[str, npt.NDArray[Any]]) -> int:
    lengths = {name: len(array) for name, array in fields.items()}
    distinct = set(lengths.values())
    if len(distinct) != 1:
        raise SchemaError(f"columns must share one length, got {lengths}")
    return distinct.pop()


@dataclass(frozen=True, slots=True)
class TradeLog:
    """One record per closed trade, stored columnwise.

    Attributes
    ----------
    trade_id : numpy.ndarray of str
        Unique identifier per trade.
    symbol : numpy.ndarray of str
        Canonical symbol, resolved by the adapter against the symbology map.
    side : numpy.ndarray of int8
        ``Side.LONG`` or ``Side.SHORT``, that is ``+1`` or ``-1``.
    qty, multiplier : numpy.ndarray of float64
        Both strictly positive. ``multiplier`` has no default: a silent default
        of 1 would misprice futures P&L by orders of magnitude without raising,
        which is the worst available failure mode. See D007.
    entry_ns, exit_ns : numpy.ndarray of int64
        UTC nanoseconds since the epoch.
    entry_px, exit_px : numpy.ndarray of float64
    fees : numpy.ndarray of float64
        Non negative magnitude of total cost. ``pnl`` is already net of it.
    pnl : numpy.ndarray of float64
        In account currency, net of fees.
    tags : tuple of mapping
        Free form per trade metadata, used to group by setup or parameter.

    Notes
    -----
    Records are ordered by ``exit_ns``, ties broken by ``trade_id``. Exit order
    is the realisation order of P&L, and it is also the attribution order used
    to build ``PeriodReturns``, so using it for both keeps the two series
    consistent with each other.

    Value level invariants, including the P&L coherence identity, are checked
    by ``qvalid.adapters.validation.validate_trade_log`` at the boundary, where
    tick size and multiplier are available from the symbology map. ``core``
    assumes a validated contract and does not revalidate. See D007 and ``04``.
    """

    trade_id: npt.NDArray[np.str_]
    symbol: npt.NDArray[np.str_]
    side: SideArray
    qty: FloatArray
    multiplier: FloatArray
    entry_ns: IntArray
    exit_ns: IntArray
    entry_px: FloatArray
    exit_px: FloatArray
    fees: FloatArray
    pnl: FloatArray
    tags: tuple[Mapping[str, Any], ...] = field(default=())

    def __post_init__(self) -> None:
        _freeze(self.side, "side", np.int8)
        for name in ("qty", "multiplier", "entry_px", "exit_px", "fees", "pnl"):
            _freeze(getattr(self, name), name, np.float64)
        for name in ("entry_ns", "exit_ns"):
            _freeze(getattr(self, name), name, np.int64)
        for name in ("trade_id", "symbol"):
            _freeze(getattr(self, name), name, None)
            if getattr(self, name).dtype.kind not in ("U", "S"):
                raise SchemaError(f"{name} must be a string array, got {getattr(self, name).dtype}")
        n = _require_same_length(
            {
                name: getattr(self, name)
                for name in (
                    "trade_id",
                    "symbol",
                    "side",
                    "qty",
                    "multiplier",
                    "entry_ns",
                    "exit_ns",
                    "entry_px",
                    "exit_px",
                    "fees",
                    "pnl",
                )
            }
        )
        if self.tags and len(self.tags) != n:
            raise SchemaError(f"tags must be empty or of length {n}, got {len(self.tags)}")

    @property
    def n_trades(self) -> int:
        """Number of closed trades in the log."""
        return len(self.pnl)

    def gross_pnl(self) -> FloatArray:
        """Gross P&L implied by prices, quantity, multiplier and side.

        Returns
        -------
        numpy.ndarray of float64
            ``side * (exit_px - entry_px) * qty * multiplier``.

        Notes
        -----
        This is the left hand side of the coherence identity of ``01``. It is
        exposed here because the report quotes it when the identity fails, and
        because the grid projection uses it to separate cost from raw edge.
        """
        return (
            self.side.astype(np.float64)
            * (self.exit_px - self.entry_px)
            * self.qty
            * self.multiplier
        )


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """Ordered trading sessions, in UTC, with an identifier.

    Attributes
    ----------
    calendar_id : str
        Identifier of the calendar actually used. It enters the
        ``ValidationReport``, so ``WEEKDAYS_UTC`` is a declared default rather
        than a silent one.
    session_close_ns : numpy.ndarray of int64
        Strictly increasing session close instants, UTC nanoseconds. A trade
        belongs to the first session whose close is at or after its exit.

    Notes
    -----
    Materialised in ``adapters/calendars/`` from the symbology map. ``core``
    receives it as a typed argument and never fetches it, per the dependency
    rule of ``01``.
    """

    calendar_id: str
    session_close_ns: IntArray

    def __post_init__(self) -> None:
        _freeze(self.session_close_ns, "session_close_ns", np.int64)
        if self.session_close_ns.size == 0:
            raise SchemaError("session_close_ns must not be empty")
        if not bool(np.all(np.diff(self.session_close_ns) > 0)):
            raise SchemaError("session_close_ns must be strictly increasing")

    @property
    def n_sessions(self) -> int:
        """Number of sessions covered by the calendar."""
        return int(self.session_close_ns.size)

    def sessions_per_year(self) -> float:
        """Observed session rate, used as ``periods_per_year`` for a daily grid.

        Returns
        -------
        float
            Sessions divided by the calendar span in Julian years.

        Notes
        -----
        Derived from the materialised calendar rather than from a hard coded
        252. The engine never infers this number: it is declared at the
        boundary by whichever adapter built the calendar, and it enters the
        report. See ``01`` and ``04``.
        """
        span_ns = int(self.session_close_ns[-1] - self.session_close_ns[0])
        if span_ns <= 0:
            raise SchemaError("calendar span must be positive to derive a session rate")
        years = span_ns / (365.25 * 24 * 3600 * NANOS_PER_SECOND)
        return (self.n_sessions - 1) / years


@dataclass(frozen=True, slots=True)
class TradeReturns:
    """Return per trade, in execution order. The index is trade number, not time.

    Attributes
    ----------
    values : numpy.ndarray of float64
    basis : Basis
    initial_capital : float

    Notes
    -----
    This contract deliberately carries no ``period``, no ``periods_per_year``
    and no ``calendar_id``. That absence is the enforcement mechanism of D006:
    an annualisation function cannot accept this type, because the quantities
    its formula requires do not exist on it. Statistics native to trade order,
    such as expectancy, hit rate and profit factor, are computed here and are
    never annualised.
    """

    values: FloatArray
    basis: Basis
    initial_capital: float

    def __post_init__(self) -> None:
        _freeze(self.values, "values", np.float64)
        if self.initial_capital <= 0.0:
            raise SchemaError(f"initial_capital must be positive, got {self.initial_capital}")

    @property
    def unit(self) -> Unit:
        """Index unit of this series."""
        return Unit.TRADE

    @property
    def n_trades(self) -> int:
        """Number of trade returns."""
        return int(self.values.size)


@dataclass(frozen=True, slots=True)
class PeriodReturns:
    """Return per calendar period. The only admissible source of any annualised number.

    Attributes
    ----------
    values : numpy.ndarray of float64
        One return per period of the grid, including periods with no trade.
    period_end_ns : numpy.ndarray of int64
        Closing instant of each period, UTC nanoseconds, strictly increasing.
    period : Period
    periods_per_year : float
        Declared at the boundary, never inferred by the engine. For a daily
        grid it comes from :meth:`TradingCalendar.sessions_per_year`.
    calendar_id : str
    basis : Basis
    initial_capital : float
    n_active : int
        Number of periods with at least one attributed trade.

    Notes
    -----
    ``n_active`` is stored rather than derived from the count of non zero
    returns, because a scratch trade produces exactly zero P&L in an active
    period. Confusing the two would understate the active fraction and trip
    the sparsity guard for the wrong reason.
    """

    values: FloatArray
    period_end_ns: IntArray
    period: Period
    periods_per_year: float
    calendar_id: str
    basis: Basis
    initial_capital: float
    n_active: int

    def __post_init__(self) -> None:
        _freeze(self.values, "values", np.float64)
        _freeze(self.period_end_ns, "period_end_ns", np.int64)
        _require_same_length({"values": self.values, "period_end_ns": self.period_end_ns})
        if self.values.size == 0:
            raise SchemaError("values must not be empty")
        if not bool(np.all(np.diff(self.period_end_ns) > 0)):
            raise SchemaError("period_end_ns must be strictly increasing")
        if self.periods_per_year <= 0.0:
            raise SchemaError(f"periods_per_year must be positive, got {self.periods_per_year}")
        if self.initial_capital <= 0.0:
            raise SchemaError(f"initial_capital must be positive, got {self.initial_capital}")
        if not 0 <= self.n_active <= self.values.size:
            raise SchemaError(f"n_active must lie in [0, {self.values.size}], got {self.n_active}")

    @property
    def unit(self) -> Unit:
        """Index unit of this series."""
        return Unit.PERIOD

    @property
    def n_periods(self) -> int:
        """Number of calendar periods on the grid, including empty ones."""
        return int(self.values.size)

    @property
    def active_fraction(self) -> float:
        """Fraction of grid periods holding at least one attributed trade.

        Notes
        -----
        Enters the sparsity condition of the grid selection rule and the
        ``ValidationReport``. See ``02`` section 1.4 for why a low value
        invalidates the fourth moment used by the Sharpe standard error.
        """
        return self.n_active / self.n_periods

    @property
    def years(self) -> float:
        """Sample length in years implied by the grid."""
        return self.n_periods / self.periods_per_year


UNDEFINED_STATE = -1
"""Ordinal reserved for periods inside the warm up, where no label can be causal."""


@dataclass(frozen=True, slots=True)
class RegimeLabels:
    """Two ordinal labels per period, both computed from a strictly past window.

    Attributes
    ----------
    trend, volatility : numpy.ndarray of int8
        Ordinal state on each axis, or :data:`UNDEFINED_STATE` during the warm
        up. Ordinal means the values are ordered, so state 0 is the lowest
        quantile bucket, but the spacing between them carries no meaning.
    period_end_ns : numpy.ndarray of int64
    n_trend_states, n_volatility_states : int
    window : int
        Length of the trailing window, in periods, used by both estimators.
    warmup : int
        Number of leading periods left undefined.
    reference_id : str
        Identifier of the market series the labels were derived from. It enters
        the ``ValidationReport``, because relabelling against a different
        reference changes every attribution downstream.

    Notes
    -----
    A period is labelled from data ending at the **previous** period, so the
    label is knowable before the period begins. Including the period's own
    return would not be look ahead in the strict sense, since both are known by
    its close, but it would create a mechanical correlation: for a long only
    strategy an up period would be simultaneously labelled as an up trend and
    credited with profit, and the attribution would then measure the direction
    of the position rather than the regime. See D026.

    The warm up carries :data:`UNDEFINED_STATE` rather than a forced bucket.
    Quantile cuts estimated from a handful of observations are noise, and
    labelling them anyway would put that noise into the attribution with no way
    to tell it apart from a real state.
    """

    trend: npt.NDArray[np.int8]
    volatility: npt.NDArray[np.int8]
    period_end_ns: IntArray
    n_trend_states: int
    n_volatility_states: int
    window: int
    warmup: int
    reference_id: str

    def __post_init__(self) -> None:
        for name in ("trend", "volatility"):
            _freeze(getattr(self, name), name, np.int8)
        _freeze(self.period_end_ns, "period_end_ns", np.int64)
        _require_same_length(
            {
                "trend": self.trend,
                "volatility": self.volatility,
                "period_end_ns": self.period_end_ns,
            }
        )
        if self.trend.size == 0:
            raise SchemaError("regime labels must not be empty")
        if not bool(np.all(np.diff(self.period_end_ns) > 0)):
            raise SchemaError("period_end_ns must be strictly increasing")
        for name, count in (
            ("n_trend_states", self.n_trend_states),
            ("n_volatility_states", self.n_volatility_states),
        ):
            if count < 2:
                raise SchemaError(f"{name} must be at least 2, got {count}")
        for name, axis, count in (
            ("trend", self.trend, self.n_trend_states),
            ("volatility", self.volatility, self.n_volatility_states),
        ):
            defined = axis[axis != UNDEFINED_STATE]
            if defined.size and (int(defined.min()) < 0 or int(defined.max()) >= count):
                raise SchemaError(
                    f"{name} states must lie in [0, {count - 1}] or equal "
                    f"{UNDEFINED_STATE}, got range "
                    f"[{int(defined.min())}, {int(defined.max())}]"
                )
        if self.window < 2:
            raise SchemaError(f"window must be at least 2 periods, got {self.window}")
        if not 0 <= self.warmup <= self.trend.size:
            raise SchemaError(f"warmup must lie in [0, {self.trend.size}], got {self.warmup}")

    @property
    def n_periods(self) -> int:
        """Number of labelled periods, including the warm up."""
        return int(self.trend.size)

    @property
    def n_states(self) -> int:
        """Size of the joint grid."""
        return self.n_trend_states * self.n_volatility_states

    def joint(self) -> npt.NDArray[np.int8]:
        """Joint state index, or :data:`UNDEFINED_STATE` when either axis is undefined.

        Returns
        -------
        numpy.ndarray of int8
            ``trend * n_volatility_states + volatility``.

        Notes
        -----
        The joint index is ordinal only within each axis separately. State 4 of
        a three by three grid is neither better nor worse than state 3; it is a
        different pair. Any statistic that treats the joint index as a number
        rather than a label is wrong.
        """
        undefined = (self.trend == UNDEFINED_STATE) | (self.volatility == UNDEFINED_STATE)
        joint = self.trend.astype(np.int16) * self.n_volatility_states + self.volatility
        return np.ascontiguousarray(np.where(undefined, UNDEFINED_STATE, joint).astype(np.int8))

    def state_counts(self) -> dict[int, int]:
        """Count the periods in each joint state, undefined excluded."""
        joint = self.joint()
        defined = joint[joint != UNDEFINED_STATE]
        return {
            int(state): int(count)
            for state, count in zip(*np.unique(defined, return_counts=True), strict=True)
        }


@dataclass(frozen=True, slots=True)
class TrialMatrix:
    """Per period returns of every configuration that was tested, on one grid.

    Attributes
    ----------
    values : numpy.ndarray of float64, shape ``(n_periods, n_configs)``
        Column ``j`` is the return series of configuration ``j``. Every column
        covers the same periods, in the same order.
    config_ids : numpy.ndarray of str
        One identifier per column, unique.
    period_end_ns : numpy.ndarray of int64
        Closing instant of each period, strictly increasing.
    period : Period
    periods_per_year : float
    calendar_id : str
    basis : Basis
    initial_capital : float

    Notes
    -----
    The grid is declared **once**, for the whole matrix, rather than once per
    configuration. That makes the precondition of ``02`` section 3 structural:
    there is no representable state in which two configurations of the same
    matrix live on different grids, so comparing their Sharpe ratios cannot be
    a unit error. The alternative, a sequence of ``PeriodReturns`` checked for
    agreement, would leave the guarantee to a validation step that someone can
    forget.

    The matrix holds every configuration that was tested, not only the one that
    won. ``02`` section 3.3 needs the full performance matrix, and ``02``
    section 3.1 needs the dispersion across trials. A log of the winner alone
    cannot support either, which is the point of D004: the number of trials is
    an input the user supplies, never something the engine infers.
    """

    values: FloatArray
    config_ids: npt.NDArray[np.str_]
    period_end_ns: IntArray
    period: Period
    periods_per_year: float
    calendar_id: str
    basis: Basis
    initial_capital: float

    def __post_init__(self) -> None:
        _freeze(self.values, "values", np.float64, ndim=2)
        _freeze(self.period_end_ns, "period_end_ns", np.int64)
        _freeze(self.config_ids, "config_ids", None)
        if self.config_ids.dtype.kind not in ("U", "S"):
            raise SchemaError(f"config_ids must be a string array, got {self.config_ids.dtype}")
        n_periods, n_configs = self.values.shape
        if n_periods < 2 or n_configs < 1:
            raise SchemaError(
                f"values must hold at least two periods and one configuration, "
                f"got shape {self.values.shape}"
            )
        if self.config_ids.size != n_configs:
            raise SchemaError(
                f"config_ids must have one entry per column, got {self.config_ids.size} "
                f"for {n_configs} columns"
            )
        if len(set(self.config_ids.tolist())) != n_configs:
            raise SchemaError("config_ids must be unique")
        if self.period_end_ns.size != n_periods:
            raise SchemaError(
                f"period_end_ns must have one entry per period, got "
                f"{self.period_end_ns.size} for {n_periods} periods"
            )
        if not bool(np.all(np.diff(self.period_end_ns) > 0)):
            raise SchemaError("period_end_ns must be strictly increasing")
        if self.periods_per_year <= 0.0:
            raise SchemaError(f"periods_per_year must be positive, got {self.periods_per_year}")
        if self.initial_capital <= 0.0:
            raise SchemaError(f"initial_capital must be positive, got {self.initial_capital}")

    @property
    def n_periods(self) -> int:
        """Number of calendar periods covered by every configuration."""
        return int(self.values.shape[0])

    @property
    def n_configs(self) -> int:
        """Number of configurations tested."""
        return int(self.values.shape[1])

    @property
    def years(self) -> float:
        """Sample length in years implied by the shared grid."""
        return self.n_periods / self.periods_per_year

    def column(self, config_id: str) -> PeriodReturns:
        """Extract one configuration as a stand alone ``PeriodReturns``.

        Raises
        ------
        SchemaError
            If the identifier is not in the matrix.

        Notes
        -----
        The extracted series carries the grid of the matrix, so a statistic
        computed on it is comparable with one computed on any sibling column by
        construction.
        """
        matches = np.flatnonzero(self.config_ids == config_id)
        if matches.size != 1:
            raise SchemaError(
                f"configuration {config_id!r} is not in the matrix; "
                f"known identifiers are {sorted(self.config_ids.tolist())[:10]}"
            )
        column = np.ascontiguousarray(self.values[:, int(matches[0])], dtype=np.float64)
        return PeriodReturns(
            values=column,
            period_end_ns=np.ascontiguousarray(self.period_end_ns),
            period=self.period,
            periods_per_year=self.periods_per_year,
            calendar_id=self.calendar_id,
            basis=self.basis,
            initial_capital=self.initial_capital,
            n_active=int((column != 0.0).sum()),
        )


@dataclass(frozen=True, slots=True)
class EquityPaths:
    """Simulated paths, shaped ``(n_paths, n_steps)``, with provenance.

    Attributes
    ----------
    values : numpy.ndarray of float64
    unit : Unit
        Propagated from the resampled input series.
    seed : int
        The seed actually used, recorded so the run is reproducible.
    method : str
        Generator identifier, for example ``"stationary_bootstrap"``.
    period : Period or None
        Required when ``unit`` is ``PERIOD``, forbidden otherwise.

    Raises
    ------
    UnitMismatchError
        If the unit and period fields disagree. The proprietary desk simulator
        applies daily loss limits and a minimum number of traded days, which
        are meaningless over a trade indexed path.
    """

    values: FloatArray
    unit: Unit
    seed: int
    method: str
    period: Period | None = None

    def __post_init__(self) -> None:
        _freeze(self.values, "values", np.float64, ndim=2)
        if self.unit is Unit.PERIOD and self.period is None:
            raise UnitMismatchError(
                "EquityPaths with unit=PERIOD must declare a period; "
                "a calendar anchored path without a period cannot feed daily rules"
            )
        if self.unit is Unit.TRADE and self.period is not None:
            raise UnitMismatchError(
                f"EquityPaths with unit=TRADE must not declare a period, got {self.period}; "
                "trade order carries no calendar meaning"
            )

    @property
    def n_paths(self) -> int:
        """Number of simulated paths."""
        return int(self.values.shape[0])

    @property
    def n_steps(self) -> int:
        """Number of steps per path, in the declared unit."""
        return int(self.values.shape[1])
