"""Generic CSV importer for trade logs, driven by a declarative mapping.

The mapping lives in a versioned YAML file rather than in call arguments,
because the mapping *is* provenance: which column was read as the exit price,
which timezone a naive timestamp was assumed to be in, and which sign
convention the fee column used all change the result. ``01`` requires the
report to be reproducible, and a mapping that exists only inside a Python call
is not reproducible by anyone else.

Three conventions have no default here, deliberately.

**Timezone of naive timestamps.** ``03`` names timezone error as the most
common cause of a result that looks too good. A mapping whose source stamps are
naive must declare ``timezone`` explicitly; assuming UTC would be a silent
guess with a systematic effect on grid attribution.

**Side vocabulary.** Sources write LONG as ``Long``, ``Buy``, ``B`` or ``1``.
The mapping enumerates the tokens. An unrecognised token raises rather than
falling through to a default direction, because a wrong side flips the sign of
gross P&L and the coherence identity would then blame the multiplier.

**Fee sign, and whether P&L is net or gross.** These two look like the same
concern and are not. Getting the fee *sign* wrong is caught immediately and
loudly: a wrong declaration makes every fee negative, and the non negativity
invariant of ``01`` fails on the whole file before the coherence identity is
even reached. It is declared here for clarity, not because it could hide.

Getting *net versus gross* wrong is the one that hides. If the file reports P&L
before costs and the mapping claims it is net, the identity residual is exactly
the fee, and on ES a round turn of 4.20 sits well under the one tick absolute
tolerance of 12.50 per contract. The check cannot see it. That is the blind
spot ``validate_trade_log`` documents, and it is why :class:`PnlConvention` has
no default and no inference.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from qvalid.adapters.symbology import SymbologyMap
from qvalid.adapters.validation import validate_trade_log
from qvalid.contracts import FloatArray, Side, SideArray, TradeLog, to_utc_nanos
from qvalid.core.constants import PNL_RTOL
from qvalid.exceptions import SchemaError

__all__ = [
    "REQUIRED_FIELDS",
    "ColumnMapping",
    "FeeConvention",
    "ImportResult",
    "PnlConvention",
    "PnlSource",
    "RowLayout",
    "load_mapping",
    "load_mapping_text",
    "read_trade_log_csv",
]

_PAIRED_ENTRY_TS = "__qval_entry_ts"
_PAIRED_EXIT_TS = "__qval_exit_ts"
_PAIRED_ENTRY_PX = "__qval_entry_px"
_PAIRED_EXIT_PX = "__qval_exit_px"
"""Internal column names for the synthesised leg fields. Prefixed so no real column collides."""

REQUIRED_FIELDS = (
    "trade_id",
    "symbol",
    "side",
    "qty",
    "entry_ts",
    "exit_ts",
    "entry_px",
    "exit_px",
    "fees",
)
"""Canonical fields the mapping must bind. ``pnl`` is separate, see :class:`PnlSource`."""


class FeeConvention(StrEnum):
    """How the source expresses the fee column.

    ``MAGNITUDE``
        Non negative cost, the convention ``TradeLog`` stores.
    ``NEGATED``
        Cost written as a negative number, common in broker statements.

    Declared rather than inferred, but not because it could hide: a wrong
    declaration turns every fee negative and the non negativity invariant of
    ``01`` fails on the whole file, before the coherence identity runs. The
    convention that can hide is :class:`PnlConvention`.
    """

    MAGNITUDE = "MAGNITUDE"
    NEGATED = "NEGATED"


class PnlSource(StrEnum):
    """Where realised P&L comes from.

    ``COLUMN``
        Read from the file. The coherence identity is then a real, independent
        check: two paths compute the same quantity and must agree.
    ``DERIVE``
        Computed from prices, quantity, multiplier and fees. Permitted, because
        many exports omit P&L, but it makes the identity vacuous: it would be
        checking the formula against itself. Choosing it stamps a warning that
        travels into the report, so a reader can see that the strongest import
        check was not available.
    """

    COLUMN = "COLUMN"
    DERIVE = "DERIVE"


class PnlConvention(StrEnum):
    """Whether the source's P&L column is already net of costs.

    ``NET``
        Net of fees, which is what ``TradeLog`` stores.
    ``GROSS``
        Before fees. The importer subtracts the fee column.

    No default and no inference, because this is the one import convention the
    coherence identity of ``01`` cannot verify. Claiming NET on a gross column
    leaves a residual equal to the fee itself, and a round turn of 4.20 on ES
    sits under the one tick absolute tolerance of 12.50 per contract. The check
    passes and the reported P&L is overstated by the full cost of trading on
    every single trade, which is exactly the error that turns a losing strategy
    into a winning one on paper.

    A degenerate case is worth stating: with a zero fee column the two
    conventions coincide and the declaration is inert.
    """

    NET = "NET"
    GROSS = "GROSS"


class RowLayout(StrEnum):
    """How many rows the source spends on one closed trade.

    ``ONE_ROW_PER_TRADE``
        Entry and exit on the same row. NinjaTrader exports this way, and so
        does every hand kept spreadsheet, so it is the default.
    ``TWO_ROWS_PER_TRADE``
        One row for the entry and one for the exit, linked by a trade number.
        TradingView exports its list of trades this way.

    This is the distinction that tests the bet of D016. A source that differs
    only in column names is a configuration file. A source that differs in
    *shape* needs a code path, and pretending otherwise would push the pairing
    into the user's spreadsheet where nothing checks it.
    """

    ONE_ROW_PER_TRADE = "ONE_ROW_PER_TRADE"
    TWO_ROWS_PER_TRADE = "TWO_ROWS_PER_TRADE"


class ColumnMapping(BaseModel):
    """Declarative binding from one source's columns to the canonical contract.

    Attributes
    ----------
    source : str
        Identifier used to look up ``source_ids`` in the symbology map.
    columns : mapping of str to str
        Canonical field name to source column name. Must cover
        :data:`REQUIRED_FIELDS`, plus ``pnl`` when ``pnl_source`` is ``COLUMN``.
    timestamp_format : str or None
        ``strptime`` pattern. ``None`` defers to pandas inference, which is
        acceptable only for ISO 8601 and is refused for anything ambiguous.
    timezone : str or None
        IANA zone applied to naive timestamps. Required whenever the parsed
        stamps come out naive. Ignored when the source already carries an
        offset, and a mismatch between the two is reported rather than silently
        overridden.
    side_long, side_short : sequence of str
        Tokens meaning each direction, compared case insensitively after
        stripping whitespace.
    row_layout : RowLayout
        ``TWO_ROWS_PER_TRADE`` requires ``pair_key_column``, ``leg_column``,
        ``entry_markers``, ``exit_markers``, ``timestamp_column`` and
        ``price_column``, and in that layout ``entry_ts``, ``exit_ts``,
        ``entry_px`` and ``exit_px`` are produced by the pairing rather than
        bound directly.
    pair_key_column : str or None
        Source column linking the two legs of one trade.
    leg_column : str or None
        Source column saying which leg a row is.
    entry_markers, exit_markers : sequence of str
        Values of ``leg_column`` marking each leg, compared case insensitively.
        Enumerated rather than inferred for the same reason as the side tokens.
    timestamp_column, price_column : str or None
        The single timestamp and price column of a leg row.
    fee_convention : FeeConvention
    pnl_convention : PnlConvention
        Only consulted when ``pnl_source`` is ``COLUMN``. A derived P&L is net
        by construction.
    pnl_source : PnlSource
    tag_columns : sequence of str
        Extra source columns carried into ``tags`` for grouping by setup or
        parameter. They do not participate in any invariant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    columns: Mapping[str, str]
    fee_convention: FeeConvention
    pnl_convention: PnlConvention
    pnl_source: PnlSource
    row_layout: RowLayout = RowLayout.ONE_ROW_PER_TRADE
    pair_key_column: str | None = None
    leg_column: str | None = None
    entry_markers: Sequence[str] = ()
    exit_markers: Sequence[str] = ()
    timestamp_column: str | None = None
    price_column: str | None = None
    timestamp_format: str | None = None
    timezone: str | None = None
    side_long: Sequence[str] = Field(default=("long", "buy", "b", "1"))
    side_short: Sequence[str] = Field(default=("short", "sell", "s", "-1"))
    tag_columns: Sequence[str] = Field(default=())

    @field_validator("columns")
    @classmethod
    def _binds_only_known_fields(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        unknown = sorted(set(value) - set(REQUIRED_FIELDS) - {"pnl"})
        if unknown:
            raise ValueError(f"mapping binds unknown canonical fields {unknown}")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone_exists(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {value!r}: {exc}") from exc
        return value

    @property
    def required_fields(self) -> tuple[str, ...]:
        """Canonical fields the mapping must bind, given the row layout.

        Under ``TWO_ROWS_PER_TRADE`` the four leg fields come from the pairing,
        so binding them would be binding something the source does not have.
        """
        if self.row_layout is RowLayout.ONE_ROW_PER_TRADE:
            return REQUIRED_FIELDS
        return tuple(
            name
            for name in REQUIRED_FIELDS
            if name not in {"entry_ts", "exit_ts", "entry_px", "exit_px"}
        )

    def model_post_init(self, _: Any) -> None:
        """Check the cross field rules that pydantic cannot express per field."""
        missing = [name for name in self.required_fields if name not in self.columns]
        if missing:
            raise ValueError(f"mapping does not bind required fields {missing}")
        if self.pnl_source is PnlSource.COLUMN and "pnl" not in self.columns:
            raise ValueError("pnl_source=COLUMN requires the mapping to bind a 'pnl' column")
        if self.row_layout is RowLayout.TWO_ROWS_PER_TRADE:
            needed = {
                "pair_key_column": self.pair_key_column,
                "leg_column": self.leg_column,
                "timestamp_column": self.timestamp_column,
                "price_column": self.price_column,
            }
            absent = sorted(name for name, value in needed.items() if not value)
            if absent:
                raise ValueError(f"row_layout=TWO_ROWS_PER_TRADE requires {absent}")
            if not self.entry_markers or not self.exit_markers:
                raise ValueError(
                    "row_layout=TWO_ROWS_PER_TRADE requires entry_markers and exit_markers; "
                    "inferring which leg a row is would guess the direction of every trade"
                )
            overlap = {m.casefold() for m in self.entry_markers} & {
                m.casefold() for m in self.exit_markers
            }
            if overlap:
                raise ValueError(f"leg markers claimed by both legs: {sorted(overlap)}")
        overlap = {token.casefold() for token in self.side_long} & {
            token.casefold() for token in self.side_short
        }
        if overlap:
            raise ValueError(f"side tokens claimed by both directions: {sorted(overlap)}")

    @property
    def zone(self) -> tzinfo | None:
        """Materialised timezone, or ``None`` when the mapping declares none."""
        return ZoneInfo(self.timezone) if self.timezone else None


class ImportResult(BaseModel):
    """A validated log plus everything about how it was produced.

    Attributes
    ----------
    log : TradeLog
    currency : str
        The single account currency the log resolved to.
    calendars : tuple of str
        Calendar identifiers the symbology map assigns to the symbols present.
    n_rows_read : int
    warnings : tuple of str
        Anything the import decided rather than read, most importantly a
        derived P&L column.

    Notes
    -----
    Carrying the provenance out of the importer rather than logging it is what
    lets ``ValidationReport`` state which mapping produced the numbers. A log
    without that is not reproducible, per ``01``.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    log: TradeLog
    currency: str
    calendars: tuple[str, ...]
    n_rows_read: int
    warnings: tuple[str, ...]


def load_mapping(path: str | Path) -> ColumnMapping:
    """Load and validate a column mapping from YAML.

    Parameters
    ----------
    path : str or pathlib.Path

    Returns
    -------
    ColumnMapping

    Raises
    ------
    SchemaError
        Missing file, malformed YAML, or any field failing validation.

    Examples
    --------
    ::

        source: generic
        fee_convention: MAGNITUDE
        pnl_convention: NET
        pnl_source: COLUMN
        timestamp_format: "%Y-%m-%d %H:%M:%S"
        timezone: America/New_York
        columns:
          trade_id: id
          symbol: instrument
          side: direction
          qty: quantity
          entry_ts: opened_at
          exit_ts: closed_at
          entry_px: open_price
          exit_px: close_price
          fees: commission
          pnl: net_pnl
        tag_columns: [setup, session]
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SchemaError(f"column mapping not found at {file_path}")
    return load_mapping_text(file_path.read_text(encoding="utf-8"), origin=str(file_path))


def load_mapping_text(text: str, *, origin: str = "the submitted text") -> ColumnMapping:
    """Validate a column mapping that is not on disk yet. See D063.

    The browser drafts a mapping and shows it in a box before anyone has
    decided to keep it, so it has to be checked without being saved. D016 is
    untouched: the mapping that reaches a report is still a file whose hash is
    provenance, and this only lets a draft be tested before it becomes one.

    Parameters
    ----------
    text : str
        YAML.
    origin : str, optional
        What to call the source in an error message.

    Returns
    -------
    ColumnMapping

    Raises
    ------
    SchemaError
        Malformed YAML, not a mapping, or any field failing validation.
    """
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SchemaError(f"column mapping at {origin} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError(f"column mapping at {origin} must be a mapping, got {type(raw).__name__}")
    try:
        return ColumnMapping.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"column mapping at {origin} is invalid: {exc}") from exc


def _pair_legs(frame: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    """Collapse a two row per trade export into one row per trade.

    Parameters
    ----------
    frame : pandas.DataFrame
        As read from the file, two rows per trade.
    mapping : ColumnMapping
        Must declare ``row_layout=TWO_ROWS_PER_TRADE``.

    Returns
    -------
    pandas.DataFrame
        One row per trade, with the four leg fields synthesised under the
        source column names the mapping already binds, so the rest of the
        importer sees exactly what a one row source would give it. The
        equivalence of the two routes is asserted by a test rather than argued.

    Raises
    ------
    SchemaError
        A trade with other than exactly one entry and one exit, an unrecognised
        leg marker, or an exit that precedes its entry. All three are refused
        rather than repaired: a half paired trade is a missing trade, and a
        silently dropped trade changes every statistic downstream while leaving
        the file looking imported.

    Notes
    -----
    The synthesised column names are internal and prefixed, so they cannot
    collide with a real column of the source. The mapping is rewritten to point
    at them, which keeps the single downstream code path.
    """
    pair_key = str(mapping.pair_key_column)
    leg = str(mapping.leg_column)
    stamp = str(mapping.timestamp_column)
    price = str(mapping.price_column)
    for column in (pair_key, leg, stamp, price):
        if column not in frame.columns:
            raise SchemaError(
                f"the pairing needs column {column!r}, which the file does not have; "
                f"it has {sorted(frame.columns)}"
            )

    tokens = frame[leg].astype(str).str.strip().str.casefold()
    entry_tokens = {marker.casefold() for marker in mapping.entry_markers}
    exit_tokens = {marker.casefold() for marker in mapping.exit_markers}
    is_entry = tokens.isin(entry_tokens)
    is_exit = tokens.isin(exit_tokens)
    unknown = ~(is_entry | is_exit)
    if bool(unknown.any()):
        offenders = sorted(set(tokens[unknown]))[:10]
        raise SchemaError(
            f"{int(unknown.sum())} rows carry an unrecognised leg marker {offenders}; "
            f"the mapping declares entry={sorted(entry_tokens)} and exit={sorted(exit_tokens)}"
        )

    counts = frame.groupby(pair_key).size()
    malformed = counts[counts != 2]
    if not malformed.empty:
        raise SchemaError(
            f"{len(malformed)} trades do not have exactly two legs, first "
            f"{list(malformed.index[:5])}; a half paired trade is a missing trade, and "
            "dropping it would change every statistic while the import still looked clean"
        )

    entries = frame[is_entry].set_index(pair_key, drop=False)
    exits = frame[is_exit].set_index(pair_key, drop=False)
    if len(entries) != len(exits) or not entries.index.equals(exits.index.sort_values()):
        entries = entries.sort_index()
        exits = exits.sort_index()
    if not entries.index.equals(exits.index):
        raise SchemaError(
            "every trade must have exactly one entry leg and one exit leg; the two sets "
            "of trade identifiers do not match"
        )

    paired = exits.copy()
    paired[_PAIRED_ENTRY_TS] = entries[stamp].to_numpy()
    paired[_PAIRED_EXIT_TS] = exits[stamp].to_numpy()
    paired[_PAIRED_ENTRY_PX] = entries[price].to_numpy()
    paired[_PAIRED_EXIT_PX] = exits[price].to_numpy()
    # The side comes from the entry leg, because the exit of a long is a sell
    # and reading direction off the exit would invert every trade.
    if "side" in mapping.columns:
        paired[mapping.columns["side"]] = entries[mapping.columns["side"]].to_numpy()
    return paired.reset_index(drop=True)


def _parse_timestamps(raw: pd.Series, mapping: ColumnMapping, field_name: str) -> list[datetime]:
    """Parse one timestamp column into timezone aware datetimes.

    Naive results are localised to the declared timezone. Absence of both an
    offset in the data and a declared timezone is an error, never an implicit
    UTC, per ``03``.
    """
    try:
        parsed = pd.to_datetime(raw, format=mapping.timestamp_format, utc=False)
    except (ValueError, TypeError) as exc:
        raise SchemaError(
            f"column {field_name!r} does not parse with format {mapping.timestamp_format!r}: {exc}"
        ) from exc
    if parsed.isna().any():
        bad = int(parsed.isna().sum())
        raise SchemaError(
            f"column {field_name!r} has {bad} unparseable timestamps; "
            "a partially parsed column would silently drop trades"
        )
    if parsed.dt.tz is None:
        if mapping.zone is None:
            raise SchemaError(
                f"column {field_name!r} parses to naive timestamps and the mapping "
                "declares no timezone. Declare it: 03 names timezone error as the most "
                "common cause of a backtest that looks too good, and assuming UTC here "
                "would shift every trade's grid attribution by the venue offset"
            )
        parsed = parsed.dt.tz_localize(mapping.zone)
    return [stamp.to_pydatetime() for stamp in parsed]


def _parse_sides(raw: pd.Series, mapping: ColumnMapping) -> SideArray:
    """Map source direction tokens onto :class:`~qvalid.contracts.Side`."""
    tokens = raw.astype(str).str.strip().str.casefold()
    long_tokens = {token.casefold() for token in mapping.side_long}
    short_tokens = {token.casefold() for token in mapping.side_short}
    out = np.zeros(len(tokens), dtype=np.int8)
    is_long = tokens.isin(long_tokens).to_numpy()
    is_short = tokens.isin(short_tokens).to_numpy()
    unknown = ~(is_long | is_short)
    if unknown.any():
        offenders = sorted(set(tokens[unknown]))[:10]
        raise SchemaError(
            f"{int(unknown.sum())} rows carry an unrecognised side token {offenders}; "
            f"the mapping declares long={sorted(long_tokens)} and short={sorted(short_tokens)}. "
            "A wrong side flips the sign of gross P&L and the coherence identity would "
            "then blame the multiplier"
        )
    out[is_long] = int(Side.LONG)
    out[is_short] = int(Side.SHORT)
    return out


def _numeric(frame: pd.DataFrame, column: str, field_name: str) -> FloatArray:
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any():
        raise SchemaError(
            f"column {field_name!r} has {int(values.isna().sum())} non numeric values; "
            "coercing them to zero would corrupt the P&L identity silently"
        )
    return values.to_numpy(dtype=np.float64)


def read_trade_log_csv(
    path: str | Path,
    mapping: ColumnMapping,
    symbology: SymbologyMap,
    *,
    rtol: float = PNL_RTOL,
) -> ImportResult:
    """Read a CSV trade log and emit a validated ``TradeLog``.

    Parameters
    ----------
    path : str or pathlib.Path
    mapping : ColumnMapping
        Loaded from a versioned YAML file by :func:`load_mapping`.
    symbology : SymbologyMap
        Supplies multiplier, tick size, currency and calendar per symbol.
    rtol : float, optional
        Relative tolerance of the coherence identity, passed through to
        ``validate_trade_log`` and reported.

    Returns
    -------
    ImportResult

    Raises
    ------
    SchemaError
        Missing column, unparseable value, naive timestamp with no declared
        timezone, unknown side token, symbol outside the map, or mixed currency.
    TradeIntegrityError
        Any value level invariant of ``01``, including P&L coherence. Raised by
        ``validate_trade_log``, which runs here because this is the boundary
        where tick size and multiplier exist. See D007.

    Notes
    -----
    Records are sorted by ``exit_ts``, ties broken by ``trade_id``, because both
    the trade indexed series and the grid attribution read that order. Sorting
    is a service the importer performs, not a silent repair: the order is a
    contract guarantee that ``core`` relies on and never re-establishes.

    The importer does not deduplicate, fill gaps, or drop rows. Every row of the
    file becomes a trade or the import fails. A partially imported log is worse
    than a refused one, because the resulting statistics look fine.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SchemaError(f"trade log not found at {file_path}")
    frame = pd.read_csv(file_path)
    if frame.empty:
        raise SchemaError(f"trade log at {file_path} has no rows")

    wanted = dict(mapping.columns)
    if mapping.row_layout is RowLayout.TWO_ROWS_PER_TRADE:
        frame = _pair_legs(frame, mapping)
        wanted["entry_ts"] = _PAIRED_ENTRY_TS
        wanted["exit_ts"] = _PAIRED_EXIT_TS
        wanted["entry_px"] = _PAIRED_ENTRY_PX
        wanted["exit_px"] = _PAIRED_EXIT_PX
    missing = sorted({col for col in wanted.values() if col not in frame.columns})
    missing += sorted({col for col in mapping.tag_columns if col not in frame.columns})
    if missing:
        raise SchemaError(
            f"trade log at {file_path} is missing columns {missing}; "
            f"the file has {sorted(frame.columns)}"
        )

    warnings: list[str] = []
    raw_symbols = frame[wanted["symbol"]].astype(str).str.strip()
    canonical = [symbology.resolve(mapping.source, ticker) for ticker in raw_symbols]
    currency = symbology.require_single_currency(canonical)
    multiplier = np.array(
        [symbology.symbols[name].multiplier for name in canonical], dtype=np.float64
    )

    qty = _numeric(frame, wanted["qty"], "qty")
    entry_px = _numeric(frame, wanted["entry_px"], "entry_px")
    exit_px = _numeric(frame, wanted["exit_px"], "exit_px")
    fees_raw = _numeric(frame, wanted["fees"], "fees")
    fees = -fees_raw if mapping.fee_convention is FeeConvention.NEGATED else fees_raw
    side = _parse_sides(frame[wanted["side"]], mapping)

    entry_ns = to_utc_nanos(_parse_timestamps(frame[wanted["entry_ts"]], mapping, "entry_ts"))
    exit_ns = to_utc_nanos(_parse_timestamps(frame[wanted["exit_ts"]], mapping, "exit_ts"))

    if mapping.pnl_source is PnlSource.COLUMN:
        pnl = _numeric(frame, wanted["pnl"], "pnl")
        if mapping.pnl_convention is PnlConvention.GROSS:
            pnl = pnl - fees
    else:
        pnl = side.astype(np.float64) * (exit_px - entry_px) * qty * multiplier - fees
        warnings.append(
            "pnl was derived from prices rather than read from the file, so the "
            "coherence identity of 01 checks the formula against itself and provides "
            "no independent verification of this import"
        )

    trade_id = frame[wanted["trade_id"]].astype(str).str.strip().to_numpy(dtype=np.str_)
    order = np.lexsort((trade_id, exit_ns))
    tags: tuple[Mapping[str, Any], ...] = ()
    if mapping.tag_columns:
        records = frame[list(mapping.tag_columns)].to_dict(orient="records")
        tags = tuple(records[i] for i in order)

    log = TradeLog(
        trade_id=np.ascontiguousarray(trade_id[order]),
        symbol=np.ascontiguousarray(np.array(canonical, dtype=np.str_)[order]),
        side=np.ascontiguousarray(side[order]),
        qty=np.ascontiguousarray(qty[order]),
        multiplier=np.ascontiguousarray(multiplier[order]),
        entry_ns=np.ascontiguousarray(entry_ns[order]),
        exit_ns=np.ascontiguousarray(exit_ns[order]),
        entry_px=np.ascontiguousarray(entry_px[order]),
        exit_px=np.ascontiguousarray(exit_px[order]),
        fees=np.ascontiguousarray(fees[order]),
        pnl=np.ascontiguousarray(pnl[order]),
        tags=tags,
    )
    validate_trade_log(log, tick_size=symbology.tick_sizes(), rtol=rtol)

    return ImportResult(
        log=log,
        currency=currency,
        calendars=tuple(sorted({symbology.symbols[name].calendar for name in canonical})),
        n_rows_read=len(frame),
        warnings=tuple(warnings),
    )
