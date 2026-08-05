"""Boundary validation of the ``TradeLog`` contract.

Every adapter calls :func:`validate_trade_log` before emitting a ``TradeLog``.
The check lives here rather than in ``core`` because it needs tick size and
contract multiplier, which belong to the symbology map in the adapter layer.
Placing it in ``core`` would force ``core`` to import symbology and break the
dependency rule of D003. See D007.

``core`` assumes the contract already valid and does not revalidate, per
``04_convencoes_de_codigo.md``.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt

from qvalid.contracts import TradeLog
from qvalid.core.constants import PNL_RTOL
from qvalid.exceptions import TradeIntegrityError

__all__ = ["validate_trade_log"]

_MAX_LISTED_OFFENDERS = 5


def _offender_list(trade_id: npt.NDArray[np.str_], mask: npt.NDArray[np.bool_]) -> str:
    ids = trade_id[mask][:_MAX_LISTED_OFFENDERS]
    listed = ", ".join(str(x) for x in ids)
    total = int(mask.sum())
    if total > _MAX_LISTED_OFFENDERS:
        return f"first offenders: {listed}, and {total - _MAX_LISTED_OFFENDERS} more."
    return f"offenders: {listed}."


def validate_trade_log(
    log: TradeLog,
    *,
    tick_size: Mapping[str, float],
    rtol: float = PNL_RTOL,
) -> None:
    """Check every value level invariant of a ``TradeLog``.

    Parameters
    ----------
    log : TradeLog
        Contract to check. Structural invariants, meaning shapes and dtypes,
        were already enforced when the contract was constructed.
    tick_size : mapping of str to float
        Minimum price increment per canonical symbol, from the symbology map.
        Every symbol present in the log must have an entry: a missing tick size
        makes the absolute tolerance undefined, and defaulting it would let a
        wrong multiplier pass unnoticed.
    rtol : float, optional
        Relative tolerance of the P&L identity. Defaults to
        :data:`~qvalid.core.constants.PNL_RTOL`. Both tolerances enter the
        ``ValidationReport``, so the default is declared rather than silent.

    Raises
    ------
    TradeIntegrityError
        On any violated invariant. The message carries the observed value, the
        violated threshold, and the identifiers of the offending trades.

    Notes
    -----
    Checked invariants, in order:

    1. ``trade_id`` unique.
    2. ``exit_ns >= entry_ns``.
    3. ``qty > 0`` and ``multiplier > 0``.
    4. ``fees >= 0``. Fees are a magnitude, not a signed adjustment, so that
       two sign conventions cannot coexist in one field.
    5. Prices finite and positive P&L fields finite.
    6. Records ordered by ``exit_ns``.
    7. P&L coherence, by the identity of ``01``:

       ``residual = pnl - (side * (exit_px - entry_px) * qty * multiplier - fees)``

       accepted when ``abs(residual) <= max(atol, rtol * abs(gross))`` with
       ``atol = tick_size * multiplier * qty``, that is half a tick of rounding
       on each leg.

    All checks are vectorised and aggregate their violations, so one call
    reports how many trades fail and which one fails worst, rather than
    stopping at the first offender. On a mis-specified futures import the
    failure count is typically the whole file, and that count is the diagnostic.

    What this check does not catch. The absolute tolerance is one tick in
    account currency, calibrated for price rounding on the two legs. Any error
    smaller than one tick is invisible to the identity. The error that actually
    hides there is a P&L column reported gross of costs and imported as if it
    were net: the residual is then exactly one fee per trade, and on ES a round
    turn of 4.20 sits well under the one tick tolerance of 12.50 per contract.

    An earlier version of this note gave a wrong signed fee as the example. That
    one is in fact caught, and loudly: a wrong sign makes every fee negative and
    invariant 4 above fails on the whole file before this identity runs. The
    correction is recorded in D017, and the convention that cannot be inferred
    is ``PnlConvention`` in ``adapters/tradelog.py``.

    This is a deliberate trade off, not an oversight. Tightening the tolerance
    below one tick would produce false failures on any venue that reports
    rounded prices, and the errors this identity exists to catch, a wrong
    multiplier or a wrong side, are errors of magnitude, not of cents. Fee
    level reconciliation needs a separate check against a commission schedule,
    which belongs to the adapter of each broker and is out of scope here.
    """
    if log.n_trades == 0:
        raise TradeIntegrityError(
            "TradeLog is empty",
            observed=0,
            threshold=1,
        )

    unique_ids, counts = np.unique(log.trade_id, return_counts=True)
    if int(counts.max()) > 1:
        duplicated = unique_ids[counts > 1][:_MAX_LISTED_OFFENDERS]
        raise TradeIntegrityError(
            "trade_id must be unique",
            observed=int((counts > 1).sum()),
            threshold=0,
            detail=f"duplicated: {', '.join(str(x) for x in duplicated)}.",
        )

    bad = log.exit_ns < log.entry_ns
    if bool(bad.any()):
        worst = int((log.entry_ns - log.exit_ns)[bad].max())
        raise TradeIntegrityError(
            f"exit_ts must not precede entry_ts, violated by {int(bad.sum())} trades",
            observed=f"-{worst} ns",
            threshold="0 ns",
            detail=_offender_list(log.trade_id, bad),
        )

    for name in ("qty", "multiplier"):
        column: npt.NDArray[np.float64] = getattr(log, name)
        bad = ~(column > 0.0)
        if bool(bad.any()):
            raise TradeIntegrityError(
                f"{name} must be strictly positive, violated by {int(bad.sum())} trades",
                observed=float(np.nanmin(column)),
                threshold=0.0,
                detail=_offender_list(log.trade_id, bad),
            )

    bad = ~(log.fees >= 0.0)
    if bool(bad.any()):
        raise TradeIntegrityError(
            f"fees is a non negative magnitude, violated by {int(bad.sum())} trades",
            observed=float(np.nanmin(log.fees)),
            threshold=0.0,
            detail=_offender_list(log.trade_id, bad),
        )

    for name in ("entry_px", "exit_px", "pnl", "fees"):
        column = getattr(log, name)
        bad = ~np.isfinite(column)
        if bool(bad.any()):
            raise TradeIntegrityError(
                f"{name} must be finite, violated by {int(bad.sum())} trades",
                observed="non finite",
                threshold="finite",
                detail=_offender_list(log.trade_id, bad),
            )

    if log.n_trades > 1:
        out_of_order = np.diff(log.exit_ns) < 0
        if bool(out_of_order.any()):
            raise TradeIntegrityError(
                "records must be ordered by exit_ts, "
                f"violated at {int(out_of_order.sum())} positions",
                observed=int(np.argmax(out_of_order)) + 1,
                threshold="monotone non decreasing exit_ns",
            )

    missing = sorted(set(np.unique(log.symbol).tolist()) - set(tick_size))
    if missing:
        raise TradeIntegrityError(
            "tick_size missing for symbols present in the log; "
            "the absolute tolerance of the P&L identity is undefined without it",
            observed=missing,
            threshold="one entry per symbol",
        )

    tick = np.array([tick_size[str(s)] for s in log.symbol], dtype=np.float64)
    if bool((tick <= 0.0).any()):
        raise TradeIntegrityError(
            "tick_size must be strictly positive",
            observed=float(tick.min()),
            threshold=0.0,
        )

    gross = log.gross_pnl()
    residual = log.pnl - (gross - log.fees)
    atol = tick * log.multiplier * log.qty
    tolerance = np.maximum(atol, rtol * np.abs(gross))
    bad = np.abs(residual) > tolerance
    if bool(bad.any()):
        worst = int(np.argmax(np.abs(residual) - tolerance))
        raise TradeIntegrityError(
            f"P&L coherence identity violated by {int(bad.sum())} of {log.n_trades} trades; "
            f"worst at trade_id={log.trade_id[worst]!s}, "
            f"gross={float(gross[worst]):.6g}, fees={float(log.fees[worst]):.6g}, "
            f"pnl={float(log.pnl[worst]):.6g}",
            observed=float(residual[worst]),
            threshold=float(tolerance[worst]),
            detail=(
                f"rtol={rtol:g}, atol={float(atol[worst]):.6g}. "
                f"A whole file failing usually means a wrong multiplier. "
                + _offender_list(log.trade_id, bad)
            ),
        )
