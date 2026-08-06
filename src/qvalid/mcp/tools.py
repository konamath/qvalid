"""What the agent may ask the cache, and nothing it may tell it. See D075.

The point of this module is a boundary. An agent connected here can find out
what is in the cache, read a slice of it, and check that the files still match
the hashes the manifest recorded. It cannot fetch, write, delete, or reach the
network.

**Read only, and the reason is provenance rather than safety.** ``qvalid fetch``
records a manifest line for every request, including the ones that hit, which is
what makes a number in a report traceable to where it came from and what it
cost. A tool that wrote to the cache would put data there with no such line, and
the manifest would then read as complete while being wrong, which D033 calls
worse than no manifest at all.

Every tool returns plain data. Nothing here formats prose for a model to repeat:
the agent gets the numbers and the manifest fields, and what it says about them
is its own business.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from qvalid.adapters.cache import CacheKey, LocalCache
from qvalid.adapters.market import parse_two_column_csv
from qvalid.exceptions import SchemaError

__all__ = ["TOOLS", "ToolSpec", "call_tool", "tool_catalogue"]

MAX_ROWS = 5_000
"""Cap on what one read returns.

Not a safety limit, an honesty one: a model handed two hundred thousand rows
summarises them and reports the summary as if it had read the series. Above this
the tool refuses and says how many rows are there, so the caller reaches for
``qvalid fetch --out`` and a file, which is what QuantPad's own documentation
says about bulk data going through files rather than through the protocol.
"""


class ToolSpec:
    """One callable exposed to the agent, with the schema a client needs."""

    __slots__ = ("description", "handler", "name", "schema")

    def __init__(
        self,
        name: str,
        description: str,
        schema: Mapping[str, Any],
        handler: Callable[[LocalCache, Mapping[str, Any]], Any],
    ) -> None:
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler


def _coverage(cache: LocalCache, _: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every slice the cache holds, newest request last.

    Built from the manifest and not from the directory listing, because the
    manifest is the record and the directory is a consequence of it. A file
    present without a manifest line is a hole this reports by omission, and
    ``verify_cache`` is where that becomes visible.
    """
    seen: dict[str, dict[str, Any]] = {}
    for entry in cache.manifest():
        seen[entry["key"]] = {
            "source": entry["source"],
            "symbol": entry["symbol"],
            "schema": entry["schema"],
            "start": entry["start"],
            "end": entry["end"],
            "rows": entry["n_rows"],
            "bytes": entry["n_bytes"],
            "first_recorded_at": seen.get(entry["key"], {}).get(
                "first_recorded_at", entry["recorded_at"]
            ),
            "last_recorded_at": entry["recorded_at"],
            "requests": seen.get(entry["key"], {}).get("requests", 0) + 1,
            "estimated_cost": entry["estimated_cost"],
        }
    return list(seen.values())


def _read_series(cache: LocalCache, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Read one cached slice, as levels or as simple returns.

    The slice is identified the way it was stored, by source, symbol and the two
    dates, so a caller cannot ask for a window that was never fetched and get a
    silently truncated answer.
    """
    key = CacheKey(
        source=str(arguments["source"]),
        symbol=str(arguments["symbol"]),
        start=str(arguments["start"]),
        end=str(arguments["end"]),
        schema=str(arguments.get("schema", "default")),
    )
    if not cache.contains(key):
        raise SchemaError(
            f"{key.describe()} is not in the cache; fetch it with `qvalid fetch` first, "
            "which records where it came from"
        )
    series = parse_two_column_csv(cache.raw_path(key).read_bytes(), key.symbol)
    if arguments.get("as_returns"):
        series = series.to_returns()
    if series.n_observations > MAX_ROWS:
        raise SchemaError(
            f"{key.describe()} holds {series.n_observations} rows, above the {MAX_ROWS} this "
            "tool returns. Write it to a file with `qvalid fetch --out` and read that: a "
            "series summarised by a model is not a series that was read"
        )
    return {
        "series_id": series.series_id,
        "n_observations": series.n_observations,
        "n_missing": series.n_missing,
        "timestamp_ns": [int(value) for value in series.timestamp_ns],
        "values": [float(value) for value in series.values],
    }


def _verify_cache(cache: LocalCache, _: Mapping[str, Any]) -> dict[str, Any]:
    """Check every cached file against the hash the manifest recorded.

    A raw file edited by hand is detected here rather than after it has
    contaminated a report, which is the guarantee D033 bought by hashing on the
    way in.
    """
    problems = dict(cache.verify())
    return {
        "checked": len(cache.manifest()),
        "problems": problems,
        "intact": not problems,
    }


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "cache_coverage",
        "List every data slice held in the local cache, with how many times each was "
        "requested and what it cost. Read from the manifest, which records hits as well "
        "as downloads.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _coverage,
    ),
    ToolSpec(
        "read_series",
        "Read one cached slice as levels, or as simple returns. Identified by the source, "
        "symbol and date range it was stored under.",
        {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "symbol": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "schema": {"type": "string"},
                "as_returns": {"type": "boolean"},
            },
            "required": ["source", "symbol", "start", "end"],
            "additionalProperties": False,
        },
        _read_series,
    ),
    ToolSpec(
        "verify_cache",
        "Check every cached file against the hash recorded when it arrived, so a file "
        "edited by hand is found before it reaches a report.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _verify_cache,
    ),
)


def tool_catalogue() -> list[dict[str, Any]]:
    """Describe every tool in the shape a client expects from ``tools/list``."""
    return [
        {"name": tool.name, "description": tool.description, "inputSchema": dict(tool.schema)}
        for tool in TOOLS
    ]


def call_tool(cache_root: str | Path, name: str, arguments: Mapping[str, Any]) -> Any:
    """Run one tool by name.

    Raises
    ------
    SchemaError
        An unknown tool, or a missing argument. Named rather than returned as
        an empty result, because a tool that answers nothing to a question it
        did not understand teaches the caller the wrong thing about the cache.
    """
    for tool in TOOLS:
        if tool.name == name:
            try:
                return tool.handler(LocalCache(cache_root), arguments)
            except KeyError as exc:
                raise SchemaError(f"{name} needs argument {exc}") from exc
    raise SchemaError(f"no tool named {name!r}; there are {[item.name for item in TOOLS]}")
