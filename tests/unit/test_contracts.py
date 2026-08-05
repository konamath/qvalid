"""Tests for the canonical contracts.

The most important test in this file is
``TestStructuralGuaranteeOfD006::test_trade_returns_cannot_be_annualised``.
D006 is not enforced by a code review convention, it is enforced by the type
carrying no ``periods_per_year``. If someone adds that field to
``TradeReturns``, the decision has been silently reverted and this test says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pytest

from qvalid.contracts import (
    Basis,
    EquityPaths,
    Period,
    PeriodReturns,
    Side,
    TradeLog,
    TradeReturns,
    TradingCalendar,
    Unit,
    to_utc_nanos,
)
from qvalid.exceptions import SchemaError, UnitMismatchError

UTC = UTC
DAY_NS = 86_400 * 1_000_000_000


def make_log(n: int = 3, **overrides: object) -> TradeLog:
    """Build a small coherent futures log. ES like: multiplier 50, tick 0.25."""
    base = datetime(2026, 1, 5, 15, 30, tzinfo=UTC)
    entry_px = np.array([5000.0, 5010.0, 4990.0][:n], dtype=np.float64)
    exit_px = np.array([5005.0, 5000.0, 4995.0][:n], dtype=np.float64)
    side = np.array([Side.LONG, Side.SHORT, Side.LONG][:n], dtype=np.int8)
    qty = np.full(n, 2.0, dtype=np.float64)
    multiplier = np.full(n, 50.0, dtype=np.float64)
    fees = np.full(n, 4.2, dtype=np.float64)
    gross = side.astype(np.float64) * (exit_px - entry_px) * qty * multiplier
    fields: dict[str, object] = {
        "trade_id": np.array([f"T{i:03d}" for i in range(n)]),
        "symbol": np.array(["ESZ6"] * n),
        "side": side,
        "qty": qty,
        "multiplier": multiplier,
        "entry_ns": to_utc_nanos([base + timedelta(days=i) for i in range(n)]),
        "exit_ns": to_utc_nanos([base + timedelta(days=i, hours=2) for i in range(n)]),
        "entry_px": entry_px,
        "exit_px": exit_px,
        "fees": fees,
        "pnl": gross - fees,
    }
    fields.update(overrides)
    return TradeLog(**fields)  # type: ignore[arg-type]


class TestSide:
    def test_values_multiply_into_the_identity(self) -> None:
        assert int(Side.LONG) == 1
        assert int(Side.SHORT) == -1

    def test_direction_reverses_sign_of_price_move(self) -> None:
        move = 5.0
        assert Side.LONG * move == 5.0
        assert Side.SHORT * move == -5.0


class TestToUtcNanos:
    def test_rejects_naive_timestamp(self) -> None:
        with pytest.raises(SchemaError, match="naive timestamp"):
            to_utc_nanos([datetime(2026, 1, 5, 15, 30)])

    def test_converts_non_utc_zone_rather_than_rejecting(self) -> None:
        offset = timezone(timedelta(hours=-3))
        aware_local = datetime(2026, 1, 5, 12, 30, tzinfo=offset)
        aware_utc = datetime(2026, 1, 5, 15, 30, tzinfo=UTC)
        assert to_utc_nanos([aware_local])[0] == to_utc_nanos([aware_utc])[0]

    def test_epoch_is_zero(self) -> None:
        assert to_utc_nanos([datetime(1970, 1, 1, tzinfo=UTC)])[0] == 0

    def test_naive_entry_in_a_mixed_batch_is_still_rejected(self) -> None:
        with pytest.raises(SchemaError, match="position 1"):
            to_utc_nanos([datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 6)])


class TestTradeLog:
    def test_builds_and_reports_size(self) -> None:
        log = make_log(3)
        assert log.n_trades == 3

    def test_arrays_are_read_only_after_construction(self) -> None:
        log = make_log(3)
        with pytest.raises(ValueError):
            log.pnl[0] = 999.0

    def test_dataclass_is_frozen(self) -> None:
        log = make_log(3)
        with pytest.raises(Exception):
            log.pnl = np.zeros(3)  # type: ignore[misc]

    def test_length_mismatch_is_a_schema_error(self) -> None:
        with pytest.raises(SchemaError, match="one length"):
            make_log(3, fees=np.zeros(2, dtype=np.float64))

    def test_wrong_dtype_is_a_schema_error(self) -> None:
        with pytest.raises(SchemaError, match="dtype"):
            make_log(3, qty=np.full(3, 2, dtype=np.int64))

    def test_gross_pnl_matches_manual_computation(self) -> None:
        log = make_log(3)
        expected = np.array(
            [
                1 * (5005.0 - 5000.0) * 2.0 * 50.0,
                -1 * (5000.0 - 5010.0) * 2.0 * 50.0,
                1 * (4995.0 - 4990.0) * 2.0 * 50.0,
            ]
        )
        np.testing.assert_allclose(log.gross_pnl(), expected)

    def test_short_that_moved_against_the_position_loses(self) -> None:
        log = make_log(3)
        assert log.gross_pnl()[1] == pytest.approx(1000.0)

    def test_tags_length_must_match_or_be_empty(self) -> None:
        with pytest.raises(SchemaError, match="tags"):
            make_log(3, tags=({"setup": "a"},))


class TestTradingCalendar:
    def test_rejects_empty(self) -> None:
        with pytest.raises(SchemaError, match="empty"):
            TradingCalendar("WEEKDAYS_UTC", np.array([], dtype=np.int64))

    def test_rejects_unsorted(self) -> None:
        closes = np.array([3 * DAY_NS, DAY_NS, 2 * DAY_NS], dtype=np.int64)
        with pytest.raises(SchemaError, match="increasing"):
            TradingCalendar("WEEKDAYS_UTC", closes)

    def test_rejects_duplicate_sessions(self) -> None:
        closes = np.array([DAY_NS, DAY_NS], dtype=np.int64)
        with pytest.raises(SchemaError, match="increasing"):
            TradingCalendar("WEEKDAYS_UTC", closes)

    def test_session_rate_recovers_a_known_construction(self) -> None:
        """261 daily sessions spanning one year must yield a rate near 261."""
        n = 262
        closes = np.arange(n, dtype=np.int64) * DAY_NS
        calendar = TradingCalendar("EVERY_DAY_UTC", closes)
        expected = (n - 1) / ((n - 1) * DAY_NS / (365.25 * DAY_NS))
        assert calendar.sessions_per_year() == pytest.approx(expected)
        assert calendar.sessions_per_year() == pytest.approx(365.25, rel=1e-9)

    def test_weekly_calendar_gives_a_weekly_rate(self) -> None:
        closes = np.arange(60, dtype=np.int64) * 7 * DAY_NS
        calendar = TradingCalendar("WEEKLY", closes)
        assert calendar.sessions_per_year() == pytest.approx(365.25 / 7, rel=1e-9)


class TestTradeReturns:
    def test_builds(self) -> None:
        series = TradeReturns(
            values=np.array([0.01, -0.005, 0.02]),
            basis=Basis.FIXED_INITIAL,
            initial_capital=50_000.0,
        )
        assert series.n_trades == 3
        assert series.unit is Unit.TRADE

    def test_rejects_non_positive_capital(self) -> None:
        with pytest.raises(SchemaError, match="initial_capital"):
            TradeReturns(np.array([0.01]), Basis.FIXED_INITIAL, 0.0)


class TestStructuralGuaranteeOfD006:
    """The prohibition is enforced by absence of the field, not by convention."""

    @pytest.mark.parametrize("forbidden", ["periods_per_year", "period", "calendar_id", "years"])
    def test_trade_returns_cannot_be_annualised(self, forbidden: str) -> None:
        series = TradeReturns(np.array([0.01, 0.02]), Basis.FIXED_INITIAL, 1000.0)
        assert not hasattr(series, forbidden), (
            f"TradeReturns exposes {forbidden!r}. D006 has been reverted: an "
            "annualisation function could now accept a trade indexed series."
        )

    @pytest.mark.parametrize("required", ["periods_per_year", "period", "calendar_id", "years"])
    def test_period_returns_carries_what_annualisation_needs(self, required: str) -> None:
        series = PeriodReturns(
            values=np.zeros(60),
            period_end_ns=np.arange(60, dtype=np.int64) * DAY_NS,
            period=Period.DAILY,
            periods_per_year=261.0,
            calendar_id="WEEKDAYS_UTC",
            basis=Basis.FIXED_INITIAL,
            initial_capital=1000.0,
            n_active=10,
        )
        assert hasattr(series, required)

    def test_the_two_series_report_different_units(self) -> None:
        trades = TradeReturns(np.array([0.01]), Basis.FIXED_INITIAL, 1000.0)
        periods = PeriodReturns(
            values=np.zeros(60),
            period_end_ns=np.arange(60, dtype=np.int64) * DAY_NS,
            period=Period.DAILY,
            periods_per_year=261.0,
            calendar_id="WEEKDAYS_UTC",
            basis=Basis.FIXED_INITIAL,
            initial_capital=1000.0,
            n_active=10,
        )
        assert trades.unit is not periods.unit


class TestPeriodReturns:
    def build(self, **overrides: object) -> PeriodReturns:
        fields: dict[str, object] = {
            "values": np.zeros(60),
            "period_end_ns": np.arange(60, dtype=np.int64) * DAY_NS,
            "period": Period.DAILY,
            "periods_per_year": 261.0,
            "calendar_id": "WEEKDAYS_UTC",
            "basis": Basis.FIXED_INITIAL,
            "initial_capital": 100_000.0,
            "n_active": 12,
        }
        fields.update(overrides)
        return PeriodReturns(**fields)  # type: ignore[arg-type]

    def test_active_fraction(self) -> None:
        assert self.build(n_active=12).active_fraction == pytest.approx(0.2)

    def test_active_fraction_counts_periods_not_non_zero_returns(self) -> None:
        """A scratch trade produces a zero return inside an active period."""
        values = np.zeros(60)
        values[0] = 0.0  # scratch trade, still an active period
        values[1] = 0.01
        series = self.build(values=values, n_active=2)
        assert series.active_fraction == pytest.approx(2 / 60)
        assert int(np.count_nonzero(series.values)) == 1

    def test_years(self) -> None:
        assert self.build().years == pytest.approx(60 / 261.0)

    def test_rejects_empty_series(self) -> None:
        with pytest.raises(SchemaError, match="empty"):
            self.build(values=np.zeros(0), period_end_ns=np.zeros(0, dtype=np.int64))

    def test_rejects_unsorted_period_ends(self) -> None:
        ends = np.arange(60, dtype=np.int64)[::-1].copy() * DAY_NS
        with pytest.raises(SchemaError, match="increasing"):
            self.build(period_end_ns=ends)

    def test_rejects_impossible_active_count(self) -> None:
        with pytest.raises(SchemaError, match="n_active"):
            self.build(n_active=61)

    def test_rejects_non_positive_rate(self) -> None:
        with pytest.raises(SchemaError, match="periods_per_year"):
            self.build(periods_per_year=0.0)


class TestEquityPaths:
    def test_period_unit_requires_a_period(self) -> None:
        with pytest.raises(UnitMismatchError, match="must declare a period"):
            EquityPaths(np.zeros((4, 10)), Unit.PERIOD, seed=1, method="bootstrap")

    def test_trade_unit_forbids_a_period(self) -> None:
        with pytest.raises(UnitMismatchError, match="must not declare a period"):
            EquityPaths(
                np.zeros((4, 10)), Unit.TRADE, seed=1, method="bootstrap", period=Period.DAILY
            )

    def test_valid_period_paths(self) -> None:
        paths = EquityPaths(
            np.zeros((4, 10)), Unit.PERIOD, seed=7, method="bootstrap", period=Period.DAILY
        )
        assert (paths.n_paths, paths.n_steps) == (4, 10)
        assert paths.seed == 7

    def test_valid_trade_paths(self) -> None:
        paths = EquityPaths(np.zeros((4, 10)), Unit.TRADE, seed=7, method="bootstrap")
        assert paths.period is None

    def test_requires_two_dimensions(self) -> None:
        with pytest.raises(SchemaError, match="2-dimensional"):
            EquityPaths(np.zeros(10), Unit.TRADE, seed=1, method="bootstrap")

    def test_values_are_read_only(self) -> None:
        paths = EquityPaths(np.zeros((2, 3)), Unit.TRADE, seed=1, method="bootstrap")
        with pytest.raises(ValueError):
            paths.values[0, 0] = 1.0
