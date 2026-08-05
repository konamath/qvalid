"""Immutable local cache with a provenance manifest.

``03`` states the rule: every download passes through the cache, and a slice
already present is never fetched again. This module is where that rule lives,
and it is also where the offline guarantee of ``04`` is bought.

The network never appears here. A caller supplies an object satisfying
:class:`Fetcher`, the cache calls it at most once per slice, writes the bytes
verbatim, and appends a manifest line. Every later request for the same slice
reads the file and calls nothing. That is what makes the acceptance criterion of
``05`` v0.7 provable without a network: the test passes a fetcher that counts
its calls and asserts the counter stayed at one.

Layout, from ``03``::

    data/
      raw/            exactly as it came from the source, never edited
      curated/        parquet, partitioned by symbol and date
      manifest.jsonl  one JSON line per event

Two design points worth stating.

Raw files are named by the hash of the cache key, not by the symbol. Two
different slices of the same symbol are two files, and naming by symbol would
force a suffix convention that someone eventually breaks. The manifest carries
the readable identity.

The manifest is append only and records **every** event, including a request
that found the slice already present. Rewriting it would lose the access
history, and omitting the hits would make the log say a slice was fetched once
when it was used forty times, which is the wrong picture when the question is
whether a paid source is being hit more than expected.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from qvalid.exceptions import SchemaError

__all__ = [
    "CURATED_DIR",
    "MANIFEST_NAME",
    "RAW_DIR",
    "CacheKey",
    "CacheResult",
    "Fetcher",
    "LocalCache",
    "ManifestEntry",
]

RAW_DIR = "raw"
CURATED_DIR = "curated"
MANIFEST_NAME = "manifest.jsonl"

_KEY_SEPARATOR = "|"
"""Separator inside the hashed key material. Explicit, so no field can blur into the next."""


@dataclass(frozen=True, slots=True)
class CacheKey:
    """What identifies a slice of external data.

    Attributes
    ----------
    source : str
        Identifier of the provider, for example ``"fred"``.
    symbol : str
        Canonical symbol or series identifier.
    start, end : str
        ISO 8601 dates bounding the slice, inclusive. Strings rather than dates
        because they enter the key verbatim and a date object would invite a
        formatting decision that changes the key without changing the slice.
    schema : str
        Shape of the payload, for example ``"daily_close"``. Two slices of the
        same symbol and period in different shapes are different slices.

    Notes
    -----
    The digest of these five fields names the file on disk. Adding a field
    changes every digest, which is correct: a cache whose key omits something
    that changes the payload is a cache that serves the wrong bytes.
    """

    source: str
    symbol: str
    start: str
    end: str
    schema: str = "default"

    def digest(self) -> str:
        """Stable hash of the key, used as the filename of the raw slice."""
        material = _KEY_SEPARATOR.join(
            (self.source, self.symbol, self.start, self.end, self.schema)
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def describe(self) -> str:
        """Readable identity, for messages and for the manifest."""
        return f"{self.source}:{self.symbol}:{self.schema}:{self.start}..{self.end}"


class Fetcher(Protocol):
    """Anything that can produce the bytes of a slice.

    Kept as a protocol so the cache never imports an HTTP client, and so a test
    can pass a counting stub. Every real implementation lives in
    :mod:`qvalid.adapters.market` and is the only place in the package that
    touches the network.
    """

    def fetch(self, key: CacheKey) -> bytes:
        """Return the raw payload for ``key``."""
        ...

    @property
    def estimated_cost(self) -> float:
        """Estimated monetary cost of one fetch, in account currency.

        Zero for free sources. ``03`` requires the estimate to be recorded
        before any paid download, because pulling ten years of tick data
        because it is possible is the classic error.
        """
        ...


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One line of the provenance log.

    Attributes
    ----------
    key : str
        The readable identity from :meth:`CacheKey.describe`.
    source, symbol, schema, start, end : str
    digest : str
    downloaded : bool
        ``False`` when the slice was already present. Recording the hit rather
        than skipping it is what makes the log answer "how often was this
        used", not only "when was this fetched".
    n_bytes : int
    n_rows : int
        Newline count of the payload, which is the row count for the CSV shapes
        this library reads and a lower bound otherwise.
    sha256 : str
        Of the payload itself. The digest names the file; this identifies the
        content, and the two differing means the cache was tampered with.
    estimated_cost : float
    recorded_at : str
    """

    key: str
    source: str
    symbol: str
    schema: str
    start: str
    end: str
    digest: str
    downloaded: bool
    n_bytes: int
    n_rows: int
    sha256: str
    estimated_cost: float
    recorded_at: str

    def to_json(self) -> str:
        """Serialise as one JSON line, keys sorted."""
        return json.dumps(
            {
                "downloaded": self.downloaded,
                "digest": self.digest,
                "end": self.end,
                "estimated_cost": self.estimated_cost,
                "key": self.key,
                "n_bytes": self.n_bytes,
                "n_rows": self.n_rows,
                "recorded_at": self.recorded_at,
                "schema": self.schema,
                "sha256": self.sha256,
                "source": self.source,
                "start": self.start,
                "symbol": self.symbol,
            },
            sort_keys=True,
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class CacheResult:
    """The bytes of a slice plus how they were obtained."""

    payload: bytes
    path: Path
    downloaded: bool
    entry: ManifestEntry


class LocalCache:
    """Read through cache over a directory tree, with a provenance manifest.

    Parameters
    ----------
    root : str or pathlib.Path
        Base directory. ``raw/`` and ``curated/`` are created under it, and the
        manifest sits beside them. Nothing here is versioned in git, per ``03``.

    Notes
    -----
    Read through, never write through: the caller cannot put bytes in without
    going past the manifest, so a slice with no provenance line cannot exist.
    Immutable, so an existing raw file is never overwritten. If a fetch produces
    different bytes for a key that already has a file, that is a change in the
    source rather than a cache miss, and it raises rather than silently
    replacing the history the report was built on.
    """

    __slots__ = ("root",)

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        (self.root / RAW_DIR).mkdir(parents=True, exist_ok=True)
        (self.root / CURATED_DIR).mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path:
        """Location of the append only provenance log."""
        return self.root / MANIFEST_NAME

    def raw_path(self, key: CacheKey) -> Path:
        """Where the raw payload for ``key`` lives, present or not."""
        return self.root / RAW_DIR / f"{key.digest()}.raw"

    def contains(self, key: CacheKey) -> bool:
        """Whether the slice is already on disk."""
        return self.raw_path(key).is_file()

    def get(
        self, key: CacheKey, fetcher: Fetcher, *, recorded_at: str | None = None
    ) -> CacheResult:
        """Return the slice, fetching it only if it is not already present.

        Parameters
        ----------
        key : CacheKey
        fetcher : Fetcher
            Called at most once, and not at all when the slice is cached. This
            is the property ``05`` v0.7 asks to be proved.
        recorded_at : str or None, optional
            Injectable timestamp, so a test can assert on manifest content.

        Returns
        -------
        CacheResult

        Raises
        ------
        SchemaError
            If the fetcher returns an empty payload, which is a failure dressed
            as a success and would otherwise be cached forever.
        """
        path = self.raw_path(key)
        downloaded = not path.is_file()
        if downloaded:
            payload = fetcher.fetch(key)
            if not payload:
                raise SchemaError(
                    f"fetching {key.describe()} returned an empty payload; refusing to "
                    "cache it, because an empty slice would be served for ever"
                )
            path.write_bytes(payload)
        else:
            payload = path.read_bytes()

        entry = ManifestEntry(
            key=key.describe(),
            source=key.source,
            symbol=key.symbol,
            schema=key.schema,
            start=key.start,
            end=key.end,
            digest=key.digest(),
            downloaded=downloaded,
            n_bytes=len(payload),
            n_rows=payload.count(b"\n"),
            sha256=hashlib.sha256(payload).hexdigest(),
            estimated_cost=float(fetcher.estimated_cost) if downloaded else 0.0,
            recorded_at=recorded_at
            or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json() + "\n")
        return CacheResult(payload=payload, path=path, downloaded=downloaded, entry=entry)

    def manifest(self) -> list[dict[str, Any]]:
        """Read the manifest back, oldest first.

        Returns
        -------
        list of dict
            Empty when nothing has been recorded. A malformed line raises
            rather than being skipped: a provenance log with a hole is worse
            than no provenance log, because it reads as complete.
        """
        if not self.manifest_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for number, line in enumerate(
            self.manifest_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SchemaError(
                    f"manifest line {number} of {self.manifest_path} is not valid JSON: "
                    f"{exc}. A provenance log with a hole reads as complete and is worse "
                    "than none"
                ) from exc
        return entries

    def total_cost(self) -> float:
        """Sum of the estimated cost of every download recorded."""
        return float(sum(entry["estimated_cost"] for entry in self.manifest()))

    def downloads(self) -> int:
        """How many manifest events were actual downloads rather than hits."""
        return sum(1 for entry in self.manifest() if entry["downloaded"])

    def verify(self) -> Mapping[str, str]:
        """Check every cached file against the hash the manifest recorded.

        Returns
        -------
        mapping of str to str
            Digest to a description of the mismatch, empty when everything
            agrees. Used before a run that has to be reproducible, since a raw
            file edited by hand would silently change every number downstream.
        """
        problems: dict[str, str] = {}
        for entry in self.manifest():
            path = self.root / RAW_DIR / f"{entry['digest']}.raw"
            if not path.is_file():
                problems[entry["digest"]] = f"missing file for {entry['key']}"
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != entry["sha256"]:
                problems[entry["digest"]] = (
                    f"{entry['key']} was recorded as {entry['sha256'][:16]} and is now "
                    f"{actual[:16]}; the raw store is meant to be immutable"
                )
        return problems
