"""Fetchers for external series, and the only module in the package that touches the network.

Everything else goes through :mod:`qvalid.adapters.cache`, which takes a
:class:`~qvalid.adapters.cache.Fetcher` and never imports an HTTP client. Keeping
the network in exactly one file is what makes the offline guarantee of ``04``
checkable by inspection rather than by hope: if no test imports this module's
fetchers, no test can reach a socket.

Parsing is separate from fetching, and the split matters. A parser takes bytes
and returns a series, so it is testable against a canned payload with no
network. A fetcher takes a key and returns bytes, so it is the only thing a test
has to avoid. Every function here is on one side of that line, never both.

API keys come from the environment, per ``04``. A key must not reach the run
configuration, because the configuration is versioned and its hash goes into the
report, and a secret that travels with provenance is a secret that leaks.

References
----------
Federal Reserve Bank of St. Louis, FRED API documentation.
https://fred.stlouisfed.org/docs/api/fred/
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from qvalid.adapters.cache import CacheKey, Fetcher, LocalCache
from qvalid.adapters.timestamps import to_utc_nanos_from_pandas
from qvalid.contracts import FloatArray, IntArray
from qvalid.exceptions import InsufficientSampleError, SchemaError

__all__ = [
    "FRED_API_KEY_ENV",
    "FRED_BASE_URL",
    "FileFetcher",
    "FredFetcher",
    "MarketSeries",
    "load_series",
    "parse_fred_csv",
    "parse_two_column_csv",
]

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_KEY_ENV = "QVALID_FRED_API_KEY"
"""Environment variable holding the FRED key. Never the config file, never the report."""

_FRED_MISSING = "."
"""FRED writes a missing observation as a single dot rather than as an empty field."""


class Parser(Protocol):
    """Takes bytes and a series identifier, returns a parsed series."""

    def __call__(self, payload: bytes, series_id: str) -> MarketSeries:
        """Parse ``payload`` into a series."""
        ...


@dataclass(frozen=True, slots=True)
class MarketSeries:
    """A parsed external series, aligned to nothing yet.

    Attributes
    ----------
    timestamp_ns : numpy.ndarray of int64
        UTC nanoseconds, strictly increasing.
    values : numpy.ndarray of float64
    series_id : str
    n_missing : int
        Observations the source marked as missing and this parser dropped. It
        is reported rather than absorbed, because a series with a fifth of its
        observations missing is a different object from a complete one and the
        caller has to know which they got.

    Notes
    -----
    Alignment to a trading grid happens in the pipeline and is by timestamp, per
    D032. This type deliberately does not resample, forward fill or reindex:
    every one of those is a modelling decision, and making it silently inside a
    parser is how a look ahead gets introduced.
    """

    timestamp_ns: IntArray
    values: FloatArray
    series_id: str
    n_missing: int

    def __post_init__(self) -> None:
        if self.timestamp_ns.size != self.values.size:
            raise SchemaError(
                f"timestamps and values must have the same length, got "
                f"{self.timestamp_ns.size} and {self.values.size}"
            )
        if self.timestamp_ns.size and not bool(np.all(np.diff(self.timestamp_ns) > 0)):
            raise SchemaError(f"timestamps of {self.series_id} must be strictly increasing")

    def to_returns(self) -> MarketSeries:
        """Convert a level series to simple returns, dropping the first observation.

        The regime grid of ``02`` section 4 is built on returns, and every free
        source that carries an index carries it as a level. Simple and not log
        returns because the labelling compares a realised return against
        quantiles of its own past, and the monotone transform between the two
        would move every quantile without changing any ordering, which is a
        difference nobody could read.

        The first observation is dropped rather than set to zero. A zero would
        say the market did not move on a day it was not observed, which is the
        same error as filling a trial matrix outside a variant's own span.
        """
        if self.values.size < 2:
            raise InsufficientSampleError(
                f"a return series needs at least two levels, {self.series_id} has "
                f"{self.values.size}",
                observed=self.values.size,
                threshold=2,
            )
        levels = np.asarray(self.values, dtype=np.float64)
        if np.any(levels[:-1] == 0.0):
            raise SchemaError(
                f"{self.series_id} holds a zero level, so a simple return is undefined there"
            )
        return MarketSeries(
            timestamp_ns=np.ascontiguousarray(self.timestamp_ns[1:]),
            values=np.ascontiguousarray(levels[1:] / levels[:-1] - 1.0),
            series_id=f"{self.series_id}:returns",
            n_missing=self.n_missing,
        )

    def to_reference_csv(self) -> str:
        """Serialise into the two column form :func:`~qvalid.pipeline._load_reference` reads.

        Header, an ISO 8601 timezone aware period close, and a return. D032
        makes the alignment exact by timestamp, so a source whose calendar
        differs from the run's grid is refused there by name rather than
        silently reindexed here.
        """
        lines = ["period_end,ret"]
        for stamp, value in zip(self.timestamp_ns, self.values, strict=True):
            moment = datetime.fromtimestamp(int(stamp) / 1e9, tz=UTC)
            lines.append(f"{moment.isoformat()},{float(value)!r}")
        return "\n".join(lines) + "\n"

    @property
    def n_observations(self) -> int:
        """Number of usable observations."""
        return int(self.values.size)


class FredFetcher:
    """Fetch a FRED series through the official observations endpoint.

    Parameters
    ----------
    api_key : str or None, optional
        ``None`` reads :data:`FRED_API_KEY_ENV` from the environment. A missing
        key raises at construction rather than at fetch time, so a run that
        cannot possibly succeed fails before it starts doing work.
    timeout : float, optional

    Notes
    -----
    FRED is free, so :attr:`estimated_cost` is zero. The attribute exists
    anyway, because the manifest records it and a paid source dropped in later
    must not require the manifest schema to change.

    The fetch method is the only place in the package that opens a socket. It
    is deliberately three lines long: everything that can be tested has been
    moved into :func:`parse_fred_csv`, which takes bytes.
    """

    __slots__ = ("_api_key", "_timeout")

    def __init__(self, api_key: str | None = None, *, timeout: float = 30.0) -> None:
        resolved = api_key or os.environ.get(FRED_API_KEY_ENV)
        if not resolved:
            raise SchemaError(
                f"no FRED API key: set {FRED_API_KEY_ENV} in the environment. It must not "
                "go in the run configuration, because the configuration is versioned and "
                "its hash enters the report"
            )
        self._api_key = resolved
        self._timeout = timeout

    def url_for(self, key: CacheKey) -> str:
        """Build the request URL for a slice.

        Exposed so the query construction can be asserted without a network.
        The URL carries the API key, so it must never be logged, printed or put
        in the manifest.
        """
        query = urlencode(
            {
                "series_id": key.symbol,
                "observation_start": key.start,
                "observation_end": key.end,
                "file_type": "json",
                "api_key": self._api_key,
            }
        )
        return f"{FRED_BASE_URL}?{query}"

    def fetch(self, key: CacheKey) -> bytes:  # pragma: no cover
        """Perform the request. The only socket in the package.

        Not covered by tests on purpose: ``04`` forbids network dependence in
        the suite, and a test that mocked ``urlopen`` would be testing the mock.
        What can be tested is tested, in :meth:`url_for` and in
        :func:`parse_fred_csv`.
        """
        from urllib.request import urlopen

        with urlopen(self.url_for(key), timeout=self._timeout) as response:
            return bytes(response.read())

    @property
    def estimated_cost(self) -> float:
        """Zero. FRED is free, and ``03`` puts it in the free catalogue."""
        return 0.0


class FileFetcher:
    """Read a slice from a local file instead of the network.

    The point of this class is not convenience. It is that a run over data the
    user already has on disk goes through the **same** cache and writes the
    **same** manifest line as a run that downloaded it, so the provenance of the
    two is described in one vocabulary. A path that bypassed the cache would
    produce a report whose data has no recorded origin.

    Parameters
    ----------
    path : str
        Source file. Read as bytes and passed through untouched.
    cost : float, optional
        Declared cost of obtaining this file, if it was bought. Defaults to
        zero and enters the manifest either way.
    """

    __slots__ = ("_cost", "_path")

    def __init__(self, path: str, *, cost: float = 0.0) -> None:
        self._path = path
        self._cost = cost

    def fetch(self, key: CacheKey) -> bytes:
        """Return the file's bytes."""
        source = Path(self._path)
        if not source.is_file():
            raise SchemaError(f"cannot supply {key.describe()}: no file at {source}")
        return source.read_bytes()

    @property
    def estimated_cost(self) -> float:
        """Declared cost of this file."""
        return self._cost


def parse_fred_csv(payload: bytes, series_id: str) -> MarketSeries:
    """Parse the two column CSV shape FRED serves for a graph download.

    Parameters
    ----------
    payload : bytes
        First column a date, second the value. FRED writes a missing
        observation as a single dot.
    series_id : str

    Returns
    -------
    MarketSeries

    Raises
    ------
    SchemaError
        Fewer than two columns, unparseable dates, or an entirely missing
        series. The last one matters: a series of nothing but dots parses to an
        empty array, and an empty reference series would make every regime
        undefined without anything looking wrong.
    """
    frame = _read_two_columns(payload, series_id)
    raw = frame.iloc[:, 1].astype(str).str.strip()
    missing = raw == _FRED_MISSING
    return _build_series(frame, raw.where(~missing), series_id, int(missing.sum()))


def parse_two_column_csv(payload: bytes, series_id: str) -> MarketSeries:
    """Parse a generic date and value CSV, with empty fields marking missing."""
    frame = _read_two_columns(payload, series_id)
    raw = frame.iloc[:, 1]
    numeric = pd.to_numeric(raw, errors="coerce")
    return _build_series(frame, numeric, series_id, int(numeric.isna().sum()))


def _read_two_columns(payload: bytes, series_id: str) -> pd.DataFrame:
    frame = pd.read_csv(BytesIO(payload))
    if frame.shape[1] < 2:
        raise SchemaError(
            f"{series_id} must have a date column and a value column, got {list(frame.columns)}"
        )
    return frame


def _build_series(
    frame: pd.DataFrame, values: pd.Series, series_id: str, n_missing: int
) -> MarketSeries:
    numeric = pd.to_numeric(values, errors="coerce")
    try:
        stamps = pd.to_datetime(frame.iloc[:, 0], utc=True)
    except (ValueError, TypeError) as exc:
        raise SchemaError(f"the date column of {series_id} does not parse: {exc}") from exc
    if stamps.isna().any():
        raise SchemaError(
            f"{int(stamps.isna().sum())} dates of {series_id} do not parse; a partially "
            "parsed series would silently drop observations"
        )
    usable = ~numeric.isna()
    if not bool(usable.any()):
        raise SchemaError(
            f"{series_id} has no usable observation; an empty reference series would make "
            "every regime undefined with nothing looking wrong"
        )
    return MarketSeries(
        timestamp_ns=to_utc_nanos_from_pandas(stamps[usable], source=series_id),
        values=np.ascontiguousarray(numeric[usable].to_numpy(), dtype=np.float64),
        series_id=series_id,
        n_missing=n_missing,
    )


def load_series(
    cache: LocalCache,
    key: CacheKey,
    fetcher: Fetcher,
    *,
    parser: Parser = parse_two_column_csv,
    recorded_at: str | None = None,
) -> MarketSeries:
    """Fetch a slice through the cache and parse it.

    Parameters
    ----------
    cache : LocalCache
    key : CacheKey
    fetcher : Fetcher
        Called at most once, and not at all when the slice is already present.
    parser : callable, optional
        Takes bytes and a series identifier. Defaults to the generic two column
        reader; pass :func:`parse_fred_csv` for FRED.
    recorded_at : str or None, optional

    Returns
    -------
    MarketSeries

    Notes
    -----
    This is the only function a caller needs. Going around it, by reading a raw
    file directly, produces a series with no manifest line, and ``03`` is
    explicit that a result without data provenance is not reproducible.
    """
    result = cache.get(key, fetcher, recorded_at=recorded_at)
    return parser(result.payload, key.symbol)
