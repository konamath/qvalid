"""Tests for boundary validation of the TradeLog contract.

The motivating case of D007 is ``TestPnlCoherence::test_missing_multiplier_is_caught``:
a futures log imported with multiplier 1 must fail loudly rather than produce a
P&L wrong by a factor of 50 with no error at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from qvalid.adapters.validation import validate_trade_log
from qvalid.contracts import Side, TradeLog, to_utc_nanos
from qvalid.exceptions import TradeIntegrityError

UTC = UTC
ES_TICK = {"ESZ6": 0.25}
STOCK_TICK = {"AAPL": 0.01}


def build(
    n: int = 4,
    *,
    multiplier: float = 50.0,
    symbol: str = "ESZ6",
    **overrides: object,
) -> TradeLog:
    base = datetime(2026, 1, 5, 15, 30, tzinfo=UTC)
    rng = np.random.default_rng(20260804)
    entry_px = 5000.0 + rng.normal(0.0, 5.0, n).round(2)
    move = rng.normal(0.0, 3.0, n).round(2)
    side = np.where(rng.random(n) < 0.5, Side.LONG, Side.SHORT).astype(np.int8)
    qty = np.full(n, 2.0, dtype=np.float64)
    mult = np.full(n, multiplier, dtype=np.float64)
    fees = np.full(n, 4.2, dtype=np.float64)
    exit_px = entry_px + move
    gross = side.astype(np.float64) * move * qty * mult
    fields: dict[str, object] = {
        "trade_id": np.array([f"T{i:03d}" for i in range(n)]),
        "symbol": np.array([symbol] * n),
        "side": side,
        "qty": qty,
        "multiplier": mult,
        "entry_ns": to_utc_nanos([base + timedelta(days=i) for i in range(n)]),
        "exit_ns": to_utc_nanos([base + timedelta(days=i, hours=2) for i in range(n)]),
        "entry_px": entry_px,
        "exit_px": exit_px,
        "fees": fees,
        "pnl": gross - fees,
    }
    fields.update(overrides)
    return TradeLog(**fields)  # type: ignore[arg-type]


class TestHappyPath:
    def test_coherent_futures_log_passes(self) -> None:
        validate_trade_log(build(50), tick_size=ES_TICK)

    def test_coherent_equity_log_passes(self) -> None:
        log = build(20, multiplier=1.0, symbol="AAPL")
        validate_trade_log(log, tick_size=STOCK_TICK)

    def test_single_trade_log_passes(self) -> None:
        validate_trade_log(build(1), tick_size=ES_TICK)

    def test_zero_fee_log_passes(self) -> None:
        log = build(10)
        validate_trade_log(
            TradeLog(
                trade_id=log.trade_id,
                symbol=log.symbol,
                side=log.side,
                qty=log.qty,
                multiplier=log.multiplier,
                entry_ns=log.entry_ns,
                exit_ns=log.exit_ns,
                entry_px=log.entry_px,
                exit_px=log.exit_px,
                fees=np.zeros(10),
                pnl=log.gross_pnl(),
            ),
            tick_size=ES_TICK,
        )


class TestPnlCoherence:
    def test_half_tick_rounding_on_each_leg_is_tolerated(self) -> None:
        """One full tick of residue, the documented atol, must pass."""
        log = build(6)
        one_tick_value = 0.25 * 50.0 * 2.0
        perturbed = TradeLog(
            trade_id=log.trade_id,
            symbol=log.symbol,
            side=log.side,
            qty=log.qty,
            multiplier=log.multiplier,
            entry_ns=log.entry_ns,
            exit_ns=log.exit_ns,
            entry_px=log.entry_px,
            exit_px=log.exit_px,
            fees=log.fees,
            pnl=log.pnl + 0.999 * one_tick_value,
        )
        validate_trade_log(perturbed, tick_size=ES_TICK)

    def test_ten_ticks_of_residue_fails(self) -> None:
        log = build(6)
        one_tick_value = 0.25 * 50.0 * 2.0
        perturbed = TradeLog(
            trade_id=log.trade_id,
            symbol=log.symbol,
            side=log.side,
            qty=log.qty,
            multiplier=log.multiplier,
            entry_ns=log.entry_ns,
            exit_ns=log.exit_ns,
            entry_px=log.entry_px,
            exit_px=log.exit_px,
            fees=log.fees,
            pnl=log.pnl + 10.0 * one_tick_value,
        )
        with pytest.raises(TradeIntegrityError) as excinfo:
            validate_trade_log(perturbed, tick_size=ES_TICK)
        message = str(excinfo.value)
        assert "observed=" in message and "threshold=" in message
        assert excinfo.value.observed == pytest.approx(10.0 * one_tick_value, rel=1e-9)
        assert excinfo.value.threshold == pytest.approx(one_tick_value, rel=1e-9)

    def test_missing_multiplier_is_caught(self) -> None:
        """D007 motivating case: futures log imported as if multiplier were 1.

        The P&L column is correct for a multiplier of 50, the contract declares
        1, and every trade is off by a factor of 50. Assuming a default of 1
        would let this through silently, which is the worst failure mode
        available: a plausible looking Sharpe computed on a P&L wrong by orders
        of magnitude.
        """
        good = build(40, multiplier=50.0)
        mis_imported = TradeLog(
            trade_id=good.trade_id,
            symbol=good.symbol,
            side=good.side,
            qty=good.qty,
            multiplier=np.ones(40),
            entry_ns=good.entry_ns,
            exit_ns=good.exit_ns,
            entry_px=good.entry_px,
            exit_px=good.exit_px,
            fees=good.fees,
            pnl=good.pnl,
        )
        with pytest.raises(TradeIntegrityError) as excinfo:
            validate_trade_log(mis_imported, tick_size=ES_TICK)
        message = str(excinfo.value)
        assert "40 of 40 trades" in message
        assert "wrong multiplier" in message

    def test_sign_convention_of_short_is_enforced(self) -> None:
        """Flipping every side must break the identity on the trades that moved."""
        log = build(30)
        flipped = TradeLog(
            trade_id=log.trade_id,
            symbol=log.symbol,
            side=(-log.side.astype(np.int8)).astype(np.int8),
            qty=log.qty,
            multiplier=log.multiplier,
            entry_ns=log.entry_ns,
            exit_ns=log.exit_ns,
            entry_px=log.entry_px,
            exit_px=log.exit_px,
            fees=log.fees,
            pnl=log.pnl,
        )
        with pytest.raises(TradeIntegrityError, match="coherence identity"):
            validate_trade_log(flipped, tick_size=ES_TICK)

    def test_fee_sign_error_above_one_tick_is_caught(self) -> None:
        """pnl = gross + fees instead of gross - fees, with fees above the floor."""
        log = build(20, fees=np.full(20, 200.0))
        wrong_sign = TradeLog(
            trade_id=log.trade_id,
            symbol=log.symbol,
            side=log.side,
            qty=log.qty,
            multiplier=log.multiplier,
            entry_ns=log.entry_ns,
            exit_ns=log.exit_ns,
            entry_px=log.entry_px,
            exit_px=log.exit_px,
            fees=log.fees,
            pnl=log.gross_pnl() + log.fees,
        )
        with pytest.raises(TradeIntegrityError, match="coherence identity"):
            validate_trade_log(wrong_sign, tick_size=ES_TICK)

    def test_fee_sign_error_below_one_tick_is_not_detectable(self) -> None:
        """Documented blind spot, not a bug. See validate_trade_log Notes.

        One ES tick on two contracts is 25.00 in account currency. A round turn
        fee of 4.20 booked with the wrong sign moves the residual by 8.40, which
        sits under the absolute floor. The identity exists to catch errors of
        magnitude, a wrong multiplier or a wrong side, not errors of cents.
        Tightening the floor below one tick would fail every venue that reports
        rounded prices.
        """
        log = build(20)
        wrong_sign = TradeLog(
            trade_id=log.trade_id,
            symbol=log.symbol,
            side=log.side,
            qty=log.qty,
            multiplier=log.multiplier,
            entry_ns=log.entry_ns,
            exit_ns=log.exit_ns,
            entry_px=log.entry_px,
            exit_px=log.exit_px,
            fees=log.fees,
            pnl=log.gross_pnl() + log.fees,
        )
        validate_trade_log(wrong_sign, tick_size=ES_TICK)
        residual = 2 * 4.2
        one_tick = 0.25 * 50.0 * 2.0
        assert residual < one_tick

    def test_message_names_the_worst_offender(self) -> None:
        log = build(10)
        pnl = log.pnl.copy()
        pnl[7] += 100_000.0
        broken = TradeLog(
            trade_id=log.trade_id,
            symbol=log.symbol,
            side=log.side,
            qty=log.qty,
            multiplier=log.multiplier,
            entry_ns=log.entry_ns,
            exit_ns=log.exit_ns,
            entry_px=log.entry_px,
            exit_px=log.exit_px,
            fees=log.fees,
            pnl=pnl,
        )
        with pytest.raises(TradeIntegrityError, match="T007"):
            validate_trade_log(broken, tick_size=ES_TICK)


class TestOtherInvariants:
    def test_empty_log_fails(self) -> None:
        empty = TradeLog(
            trade_id=np.array([], dtype="<U8"),
            symbol=np.array([], dtype="<U8"),
            side=np.array([], dtype=np.int8),
            qty=np.array([], dtype=np.float64),
            multiplier=np.array([], dtype=np.float64),
            entry_ns=np.array([], dtype=np.int64),
            exit_ns=np.array([], dtype=np.int64),
            entry_px=np.array([], dtype=np.float64),
            exit_px=np.array([], dtype=np.float64),
            fees=np.array([], dtype=np.float64),
            pnl=np.array([], dtype=np.float64),
        )
        with pytest.raises(TradeIntegrityError, match="empty"):
            validate_trade_log(empty, tick_size=ES_TICK)

    def test_duplicate_trade_id_fails(self) -> None:
        log = build(5)
        ids = log.trade_id.copy()
        ids[3] = ids[0]
        with pytest.raises(TradeIntegrityError, match="unique"):
            validate_trade_log(build(5, trade_id=ids), tick_size=ES_TICK)

    def test_exit_before_entry_fails(self) -> None:
        log = build(5)
        exits = log.exit_ns.copy()
        exits[2] = log.entry_ns[2] - 1
        with pytest.raises(TradeIntegrityError, match="precede"):
            validate_trade_log(build(5, exit_ns=exits), tick_size=ES_TICK)

    def test_zero_quantity_fails(self) -> None:
        qty = np.full(5, 2.0)
        qty[1] = 0.0
        with pytest.raises(TradeIntegrityError, match="qty"):
            validate_trade_log(build(5, qty=qty), tick_size=ES_TICK)

    def test_negative_fees_fail(self) -> None:
        fees = np.full(5, 4.2)
        fees[0] = -1.0
        with pytest.raises(TradeIntegrityError, match="fees"):
            validate_trade_log(build(5, fees=fees), tick_size=ES_TICK)

    def test_non_finite_price_fails(self) -> None:
        px = np.full(5, 5000.0)
        px[2] = np.nan
        with pytest.raises(TradeIntegrityError, match="finite"):
            validate_trade_log(build(5, entry_px=px), tick_size=ES_TICK)

    def test_out_of_order_records_fail(self) -> None:
        """Exits permuted while every exit still follows its own entry."""
        log = build(5)
        early_entries = np.full(5, int(log.entry_ns.min()) - 10 * 86_400 * 10**9, dtype=np.int64)
        exits = log.exit_ns.copy()
        exits[1], exits[3] = exits[3], exits[1]
        with pytest.raises(TradeIntegrityError, match="ordered by exit_ts"):
            validate_trade_log(build(5, entry_ns=early_entries, exit_ns=exits), tick_size=ES_TICK)

    def test_missing_tick_size_fails_rather_than_defaulting(self) -> None:
        with pytest.raises(TradeIntegrityError, match="tick_size missing"):
            validate_trade_log(build(5), tick_size={"NQZ6": 0.25})

    def test_message_carries_observed_and_threshold(self) -> None:
        qty = np.full(5, 2.0)
        qty[1] = -3.0
        with pytest.raises(TradeIntegrityError) as excinfo:
            validate_trade_log(build(5, qty=qty), tick_size=ES_TICK)
        assert "observed=-3.0" in str(excinfo.value)
        assert "threshold=0.0" in str(excinfo.value)


class TestScaleInvariance:
    """Invariance required by 04: the identity must not depend on units."""

    @pytest.mark.parametrize("factor", [0.5, 2.0, 1000.0])
    def test_scaling_quantity_keeps_the_log_coherent(self, factor: float) -> None:
        log = build(20)
        scaled = TradeLog(
            trade_id=log.trade_id,
            symbol=log.symbol,
            side=log.side,
            qty=log.qty * factor,
            multiplier=log.multiplier,
            entry_ns=log.entry_ns,
            exit_ns=log.exit_ns,
            entry_px=log.entry_px,
            exit_px=log.exit_px,
            fees=log.fees * factor,
            pnl=log.gross_pnl() * factor - log.fees * factor,
        )
        validate_trade_log(scaled, tick_size=ES_TICK)
