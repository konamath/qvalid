"""Recover the contract multiplier from the file, and say when that is impossible.

See D061. The coherence identity of ``01`` is

    pnl = side * (exit_px - entry_px) * qty * multiplier - fees

and :mod:`qvalid.adapters.tradelog` runs it forwards, checking a supplied
multiplier against the file. Run backwards it yields the multiplier the file
implies, per trade, which turns the second of the three configuration files
from something written blind into something checked against the data.

**It reports, it does not fill in.** D007 discarded assuming a multiplier of one
when absent, because that misprices futures by orders of magnitude without
raising anything, and that reasoning is untouched here: a number recovered from
the same file it will later be used to validate is not independent evidence.
The command prints the implied value beside an empty slot for the person to
fill from their contract specification. If the two disagree, that disagreement
is the finding.

Net versus gross
----------------
D017 established that the P&L convention is the one import declaration the
identity cannot verify, because claiming ``NET`` on a gross column leaves a
residual of exactly one fee per trade, well under a one tick tolerance. That is
correct about a per trade tolerance test and, as measured today, incomplete
about the file. Solving for the multiplier under the wrong convention gives

    m -/+ fees / (side * (exit_px - entry_px) * qty)

whose second term moves with the size of each trade's price move. The wrong
convention therefore produces a *scattered* multiplier where the right one
produces a constant, and the scatter is visible even though no single trade
violates any tolerance. See :func:`probe_symbols`.

The blind spot does not disappear, it relocates, and the new statement is
sharper and checkable: the convention is recoverable exactly when the cost per
trade exceeds the rounding granularity of the P&L column. Below that the
subtraction is destroyed by rounding, and :data:`Detectability.UNDETECTABLE`
says so rather than answering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from qvalid.adapters.tradelog import ColumnMapping, FeeConvention
from qvalid.contracts import FloatArray
from qvalid.exceptions import SchemaError

__all__ = [
    "COST_TO_QUANTUM_FLOOR",
    "MULTIPLIER_QUANTUM_CEILING",
    "Detectability",
    "SymbolProbe",
    "implied_multipliers",
    "probe_symbols",
    "probe_trade_log",
    "quantum_of",
]

COST_TO_QUANTUM_FLOOR: Final = 1.0
"""Median cost per trade, over the P&L column's quantum, below which we refuse.

Measured over six seeds per point, on tick rounded futures prices with the P&L
column rounded to a varying quantum. The quantity swept is the ratio between
the dispersion of the implied multiplier under the wrong convention and under
the right one, so above one the right convention is the tighter of the two and
below one the comparison points at the wrong answer:

    cost / quantum   0.06   0.13   0.32   0.63   1.26   3.15   6.30   12.60
    ratio (worst)     1.0    0.0    1.0    2.0    4.0    10.8   17.9    26.8

The transition sits between 0.32 and 0.63. One is the nearest round number
above it with margin, and at one the worst observed ratio was four. Below the
floor the arithmetic is not merely noisy, it is destroyed: subtracting a cost
smaller than the rounding step of the column it is subtracted from recovers
nothing, and a confident answer there would be the failure D017 describes.
"""


MULTIPLIER_QUANTUM_CEILING: Final = 0.25
"""P&L quantum over typical trade P&L, above which even the multiplier is gone.

:data:`COST_TO_QUANTUM_FLOOR` governs the *convention*, which dies first
because it rides on the fee. The multiplier is far more robust, since it rides
on the whole P&L, but it does die. Measured over twelve seeds per point, as
relative error of the recovered multiplier against a true 50:

    quantum / |pnl|   0.002    0.023   0.111   0.200   0.333   0.500   1.000
    error (worst)     1.4e-4   1.1e-3     0    1.1e-2  2.4e-2  6.5e-2  9.2e-1

The collapse is at one, where the rounding step reaches the size of a typical
trade's P&L and the column stops carrying the trade at all. A quarter holds the
worst case near two per cent, which is enough to tell 50 from 20 and not enough
to be mistaken for a specification. Above it :attr:`SymbolProbe.implied` is
``nan``, because a multiplier wrong by an order of magnitude that looks like a
number is precisely the failure D007 was written about.
"""


class Detectability(StrEnum):
    """Whether the file can say which P&L convention produced it.

    Attributes
    ----------
    DECISIVE
        Costs are large enough relative to the P&L column's rounding for the
        two conventions to be told apart, and one of them is the tighter.
    NO_COST
        Every fee is zero, so ``NET`` and ``GROSS`` coincide and the
        declaration is inert. Named in D017 as the degenerate case.
    UNDETECTABLE
        Costs exist but sit below :data:`COST_TO_QUANTUM_FLOOR` times the P&L
        column's quantum, so the difference between the conventions is inside
        the rounding. This is D017's blind spot, located exactly.
    """

    DECISIVE = "DECISIVE"
    NO_COST = "NO_COST"
    UNDETECTABLE = "UNDETECTABLE"


@dataclass(frozen=True, slots=True)
class SymbolProbe:
    """What one symbol's trades imply, under each convention.

    Attributes
    ----------
    symbol : str
        The source identifier as it appears in the file.
    n_usable : int
        Trades with a non zero price move. A flat trade divides by zero and
        carries no information about the multiplier, so it is excluded rather
        than regularised.
    implied_net, implied_gross : float
        Median implied multiplier, reading the P&L column as net of costs and
        as gross of them. ``nan`` when nothing was usable.
    spread_net, spread_gross : float
        Interquartile range over the median, a scale free measure of how far
        from constant the implied value is. The convention that produced the
        file is the one near zero.
    detectability : Detectability
        Whether the comparison above means anything for this symbol.
    pnl_quantum : float
        Rounding step the P&L column actually uses, from :func:`quantum_of`.
    typical_pnl : float
        Median absolute P&L over the usable trades. Together with the quantum
        this decides whether the multiplier itself survived the rounding.
    """

    symbol: str
    n_usable: int
    implied_net: float
    implied_gross: float
    spread_net: float
    spread_gross: float
    detectability: Detectability
    pnl_quantum: float
    typical_pnl: float

    @property
    def implied(self) -> float:
        """The multiplier the file implies, under either convention.

        Defined in all three states, and that is the point of separating them.
        What a small cost destroys is the *convention*, not the multiplier:
        when the fee sits below the rounding of the P&L column, the two
        inversions differ by ``fees / move``, which is exactly the quantity
        that has been rounded away, so both converge on the same number. Only
        :attr:`convention` goes silent.

        Goes to ``nan`` only when the rounding of the P&L column reaches
        :data:`MULTIPLIER_QUANTUM_CEILING` of a typical trade's P&L, at which
        point the column no longer carries the trade.

        Still a diagnostic. D007 keeps the multiplier a declared input, and
        this number's job is to disagree with the declaration when the
        declaration is wrong.
        """
        if not self.is_readable:
            return float("nan")
        return self.implied_net if self.spread_net <= self.spread_gross else self.implied_gross

    @property
    def is_readable(self) -> bool:
        """Whether the P&L column is fine enough to carry a multiplier at all."""
        if self.n_usable == 0 or self.typical_pnl <= 0.0:
            return False
        return self.pnl_quantum <= MULTIPLIER_QUANTUM_CEILING * self.typical_pnl

    @property
    def convention(self) -> str | None:
        """``"NET"``, ``"GROSS"``, or ``None`` when the file cannot say."""
        if self.detectability is not Detectability.DECISIVE:
            return None
        return "NET" if self.spread_net <= self.spread_gross else "GROSS"


def quantum_of(values: FloatArray) -> float:
    """Smallest increment the column actually uses, from the decimals present.

    A P&L column written to the cent has a quantum of ``0.01``; one rounded to
    whole currency units has ``1.0``. Read from the values rather than declared,
    because a broker that rounds is not going to mention it.

    The search runs coarse to fine and returns the **largest** step that divides
    every value, which is what makes it the quantum rather than merely a step
    that fits. An earlier version started at ``1.0``, so a column rounded to the
    nearest hundred reported ``1.0`` and the detectability gate then compared a
    real cost against a rounding a hundred times finer than the true one,
    calling decisive exactly the case the gate exists to refuse.

    Only powers of ten are considered. A column quantised to something else,
    a quarter say, reports the finest power of ten that divides it, which
    understates the true step and makes the gate conservative in the safe
    direction: it refuses more often than strictly necessary, never less.

    Returns ``0.0`` for an empty or all zero column, which no caller may treat
    as a detectable difference: it is an absence, not a fine resolution.
    """
    if values.size == 0 or not np.any(values != 0.0):
        return 0.0
    for decimals in range(-6, 9):
        step = 10.0**-decimals
        scaled = values / step
        # The tolerance scales with the magnitude because dividing a large
        # currency amount by a small step loses low order bits, and a fixed
        # absolute tolerance would call a clean column ragged once the account
        # is big enough. That would be a bug that only appears to rich people.
        slack = 1e-9 * max(1.0, float(np.max(np.abs(scaled))))
        if float(np.max(np.abs(scaled - np.round(scaled)))) <= max(slack, 1e-6):
            return step
    return 10.0**-8


def implied_multipliers(
    pnl: FloatArray,
    fees: FloatArray,
    signed_move: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    """Invert the identity for the multiplier, both ways.

    Parameters
    ----------
    pnl : ndarray
        The P&L column as read.
    fees : ndarray
        Costs as magnitudes, per ``01``.
    signed_move : ndarray
        ``side * (exit_px - entry_px) * qty``, the part of the identity the
        multiplier scales.

    Returns
    -------
    tuple of ndarray
        Implied multiplier reading the column as net, and as gross. Both are
        restricted to trades whose move is non zero, so the two are aligned
        with each other but not with the input.
    """
    usable = np.abs(signed_move) > 0.0
    move = signed_move[usable]
    return (pnl[usable] + fees[usable]) / move, pnl[usable] / move


def _spread(values: FloatArray) -> float:
    """Interquartile range over the absolute median, or ``inf`` if centred on zero."""
    if values.size == 0:
        return float("inf")
    centre = float(np.median(values))
    if centre == 0.0:
        return float("inf")
    span = float(np.percentile(values, 75) - np.percentile(values, 25))
    return abs(span / centre)


def probe_symbols(
    symbols: list[str],
    pnl: FloatArray,
    fees: FloatArray,
    signed_move: FloatArray,
) -> tuple[SymbolProbe, ...]:
    """Recover each symbol's multiplier, and each symbol's detectability.

    Parameters
    ----------
    symbols : list of str
        Source identifier per trade, parallel to the arrays.
    pnl, fees, signed_move : ndarray
        As in :func:`implied_multipliers`.

    Returns
    -------
    tuple of SymbolProbe
        One per distinct symbol, in first appearance order, because sorting
        would lose the order the person's file is in.

    Notes
    -----
    Detectability is decided per symbol, not per file. A person trading both a
    commission free instrument and a futures contract has one of each, and a
    file level verdict would export the answerable case's confidence onto the
    unanswerable one.
    """
    if not (len(symbols) == pnl.size == fees.size == signed_move.size):
        raise SchemaError(
            f"probe arrays disagree in length: {len(symbols)} symbols, {pnl.size} pnl, "
            f"{fees.size} fees, {signed_move.size} moves"
        )
    quantum = quantum_of(pnl)
    out: list[SymbolProbe] = []
    for name in dict.fromkeys(symbols):
        rows = np.array([other == name for other in symbols], dtype=bool)
        net, gross = implied_multipliers(pnl[rows], fees[rows], signed_move[rows])
        moved = pnl[rows][np.abs(signed_move[rows]) > 0.0]
        typical = float(np.median(np.abs(moved))) if moved.size else 0.0
        if not np.any(fees[rows] != 0.0):
            state = Detectability.NO_COST
        elif float(np.median(np.abs(fees[rows]))) < COST_TO_QUANTUM_FLOOR * quantum:
            state = Detectability.UNDETECTABLE
        else:
            state = Detectability.DECISIVE
        out.append(
            SymbolProbe(
                symbol=name,
                n_usable=int(net.size),
                implied_net=float(np.median(net)) if net.size else float("nan"),
                implied_gross=float(np.median(gross)) if gross.size else float("nan"),
                spread_net=_spread(net),
                spread_gross=_spread(gross),
                detectability=state,
                pnl_quantum=quantum,
                typical_pnl=typical,
            )
        )
    return tuple(out)


def probe_trade_log(path: str | Path, mapping: ColumnMapping) -> tuple[SymbolProbe, ...]:
    """Read the columns the identity needs and probe them.

    Deliberately not :func:`~qvalid.adapters.tradelog.read_trade_log`. That
    function requires a symbology map, which is the file this probe exists to
    help write, so going through it would be circular. Nothing here validates:
    the invariants of ``01`` are checked at import, against the multiplier the
    person ends up declaring, and pre empting them would move the check away
    from the number that will actually be used.

    Parameters
    ----------
    path : str or Path
        The trade log.
    mapping : ColumnMapping
        Already loaded, so a mapping still being edited can be probed without
        being saved.

    Raises
    ------
    SchemaError
        A column the identity needs is missing from the file or from the
        mapping. The P&L column is required: with ``pnl_source`` set to
        ``DERIVE`` there is nothing to invert, because the multiplier would
        then be recovered from an identity it was used to construct.
    """
    needed = ("symbol", "qty", "entry_px", "exit_px", "fees", "pnl", "side")
    absent = [field for field in needed if field not in mapping.columns]
    if absent:
        raise SchemaError(
            f"the mapping has no column for {absent}; the multiplier cannot be recovered "
            "without every term of the P&L identity"
        )
    frame = pd.read_csv(Path(path))
    missing = [mapping.columns[field] for field in needed if mapping.columns[field] not in frame]
    if missing:
        raise SchemaError(f"the file has no column named {missing}")

    def column(field: str) -> pd.Series:
        return frame[mapping.columns[field]]

    tokens = column("side").astype(str).str.strip().str.casefold()
    longs = {token.casefold() for token in mapping.side_long}
    shorts = {token.casefold() for token in mapping.side_short}
    known = tokens.isin(longs | shorts)
    if not known.all():
        raise SchemaError(
            f"{int((~known).sum())} rows carry an unrecognised side token "
            f"{sorted(set(tokens[~known]))[:10]}"
        )
    side = np.where(tokens.isin(longs).to_numpy(), 1.0, -1.0)

    fees_raw = column("fees").to_numpy(dtype=np.float64)
    fees = -fees_raw if mapping.fee_convention is FeeConvention.NEGATED else fees_raw
    move = (
        side
        * (column("exit_px") - column("entry_px")).to_numpy(dtype=np.float64)
        * column("qty").to_numpy(dtype=np.float64)
    )
    return probe_symbols(
        [str(name) for name in column("symbol")],
        column("pnl").to_numpy(dtype=np.float64),
        fees,
        move,
    )
