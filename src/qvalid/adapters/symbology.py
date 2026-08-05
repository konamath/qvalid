"""Canonical symbology map, loaded from a versioned YAML file.

Every source names instruments differently, and resolving that by string
comparison at the point of use produces silent error. ``03`` therefore makes
the map mandatory. This module is where contract multiplier, minimum tick,
currency and trading calendar enter the pipeline, which is why D007 puts the
P&L coherence check at this boundary rather than in ``core``.

Scalar configuration objects are the one place ``04`` admits pydantic: they are
small, they are read once, and a per field error message is exactly what a
misconfigured YAML file needs. The columnar contracts stay on frozen dataclasses
with NumPy columns for the reason given in ``contracts.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from qvalid.exceptions import SchemaError

__all__ = ["SymbolSpec", "SymbologyMap", "load_symbology"]


class SymbolSpec(BaseModel):
    """Everything the pipeline needs to know about one canonical symbol.

    Attributes
    ----------
    venue : str
        Exchange or market identifier, free text but required, because the same
        root trades on more than one venue with different tick sizes.
    multiplier : float
        Contract multiplier. 1 for equities and crypto. Strictly positive and
        with no default: a silent default here misprices futures P&L by orders
        of magnitude without raising, which D007 calls the worst available
        failure mode.
    tick_size : float
        Minimum price increment, strictly positive. Sets the absolute
        tolerance of the coherence identity, at one tick in account currency.
    currency : str
        Three letter code of the instrument. ``core`` assumes a single
        currency, so a log mixing currencies is refused at import.
    calendar : str
        Identifier of the trading calendar to materialise. ``WEEKDAYS_UTC``
        until v0.7 replaces it with real venue calendars.
    contract_root : str or None
        Futures root, for example ``ES``. ``None`` for cash instruments.
    source_ids : mapping of str to str
        How each source names this instrument, keyed by source identifier. The
        importer resolves a raw ticker through this before anything else.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: str
    multiplier: float = Field(gt=0.0)
    tick_size: float = Field(gt=0.0)
    currency: str
    calendar: str
    contract_root: str | None = None
    source_ids: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _currency_is_a_three_letter_code(cls, value: str) -> str:
        if len(value) != 3 or not value.isalpha():
            raise ValueError(f"currency must be a three letter code, got {value!r}")
        return value.upper()


class SymbologyMap(BaseModel):
    """The whole map, plus the reverse index from source ticker to canonical symbol.

    Notes
    -----
    The reverse index is built once at load time and any collision is an error
    rather than a last writer wins. Two canonical symbols claiming the same
    source ticker means the map itself is wrong, and discovering that during an
    import would attribute trades to the wrong instrument.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbols: Mapping[str, SymbolSpec]

    @field_validator("symbols")
    @classmethod
    def _map_is_not_empty(cls, value: Mapping[str, SymbolSpec]) -> Mapping[str, SymbolSpec]:
        if not value:
            raise ValueError("symbology map must declare at least one symbol")
        return value

    def resolve(self, source: str, raw_symbol: str) -> str:
        """Translate a source specific ticker into the canonical symbol.

        Parameters
        ----------
        source : str
            Source identifier, matching a key of ``source_ids``.
        raw_symbol : str

        Returns
        -------
        str
            Canonical symbol.

        Raises
        ------
        SchemaError
            When the ticker is unknown for that source. The message lists the
            tickers the map does know, because the usual cause is a contract
            month suffix that the map was not told about.
        """
        index = self._reverse_index(source)
        if raw_symbol in index:
            return index[raw_symbol]
        if raw_symbol in self.symbols:
            return raw_symbol
        known = sorted(set(index) | set(self.symbols))
        raise SchemaError(
            f"symbol {raw_symbol!r} is not in the symbology map for source {source!r}; "
            f"known tickers are {known}. Add it rather than letting the import guess: "
            "a wrong multiplier is the failure mode D007 exists to prevent."
        )

    def _reverse_index(self, source: str) -> dict[str, str]:
        index: dict[str, str] = {}
        for canonical, spec in self.symbols.items():
            ticker = spec.source_ids.get(source)
            if ticker is None:
                continue
            if ticker in index:
                raise SchemaError(
                    f"source {source!r} maps ticker {ticker!r} to both "
                    f"{index[ticker]!r} and {canonical!r}; the symbology map is ambiguous"
                )
            index[ticker] = canonical
        return index

    def tick_sizes(self) -> dict[str, float]:
        """Tick size per canonical symbol, in the shape ``validate_trade_log`` expects."""
        return {name: spec.tick_size for name, spec in self.symbols.items()}

    def require_single_currency(self, symbols: list[str]) -> str:
        """Return the single currency of a set of symbols, or refuse the mix.

        Raises
        ------
        SchemaError
            When more than one currency appears. ``core`` assumes a single
            account currency, and ``03`` places conversion in the adapter with
            the rate recorded in ``tags``. Until that exists, refusing is the
            honest behaviour: converting with an unrecorded rate would make the
            result irreproducible, and ignoring the mix would add unlike
            quantities.
        """
        currencies = {self.symbols[name].currency for name in symbols}
        if len(currencies) > 1:
            raise SchemaError(
                f"log mixes currencies {sorted(currencies)}; core assumes one account "
                "currency and FX conversion in the adapter is out of scope before v0.7"
            )
        return currencies.pop()


def load_symbology(path: str | Path) -> SymbologyMap:
    """Load and validate a symbology map from YAML.

    Parameters
    ----------
    path : str or pathlib.Path

    Returns
    -------
    SymbologyMap

    Raises
    ------
    SchemaError
        Missing file, malformed YAML, or any field failing validation. The
        pydantic error is reformatted rather than propagated, so callers catch
        one exception type from this layer, per ``04``.

    Examples
    --------
    ::

        symbols:
          ES:
            venue: CME
            multiplier: 50.0
            tick_size: 0.25
            currency: USD
            calendar: WEEKDAYS_UTC
            contract_root: ES
            source_ids:
              tradingview: "CME_MINI:ES1!"
              ninjatrader: "ES 03-24"
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SchemaError(f"symbology map not found at {file_path}")
    try:
        raw: Any = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchemaError(f"symbology map at {file_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError(
            f"symbology map at {file_path} must be a mapping with a 'symbols' key, "
            f"got {type(raw).__name__}"
        )
    try:
        return SymbologyMap.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"symbology map at {file_path} is invalid: {exc}") from exc
