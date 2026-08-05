"""Deterministic JSON serialisation of a :class:`~qvalid.report.model.ValidationReport`.

JSON is the reference format. The HTML and LaTeX outputs render the same
dictionary, so the three cannot disagree about a number, and any future
interface reads this rather than recomputing anything.

Determinism is the whole design constraint, because ``05`` requires two runs
over the same input with the same seed to produce byte identical reports except
for the execution timestamp. Three things follow.

Keys are sorted at every level. Python preserves insertion order in
dictionaries, so an unsorted dump would be stable within one build and would
drift the moment a field is added in a different place.

Floats go through ``repr``, which since Python 3.1 is the shortest string that
round trips. That is deterministic on a given platform and precision, which is
what the criterion asks for; it is not deterministic across platforms with
different floating point behaviour, and that limit is declared rather than
implied.

Non finite floats are refused rather than emitted. ``json`` writes ``NaN`` and
``Infinity`` by default, which are not valid JSON, so a consumer would either
reject the file or silently accept a non standard extension. Every place in this
library that can produce an undefined quantity already returns ``None`` for it,
so a non finite value arriving here is a bug and is raised as one.
"""

from __future__ import annotations

import json
import math
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

from qvalid.report.model import Evidence, ValidationReport

__all__ = ["TIMESTAMP_FIELD", "report_to_dict", "report_to_json", "write_json"]

TIMESTAMP_FIELD = "executed_at"
"""The only field expected to differ between two runs of the same input."""


def _plain(value: Any) -> Any:
    """Convert a value into something ``json`` can serialise deterministically.

    Handles the four shapes that actually occur: dataclasses, enums, NumPy
    scalars and arrays, and mappings whose keys are integers. Anything else that
    is not already a primitive raises, rather than falling back on ``str``,
    because a silent stringification would put a repr into the report and
    nobody would notice until they tried to parse it.
    """
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, int | np.integer):
        return int(value)
    if isinstance(value, float | np.floating):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(
                f"non finite value {number} reached the serialiser; every undefined "
                "quantity in this library is represented as None, so this is a bug "
                "rather than a data condition"
            )
        return number
    if isinstance(value, np.ndarray):
        return [_plain(item) for item in value.tolist()]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name))
            for field in sorted(fields(value), key=lambda f: f.name)
        }
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=_key)}
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(item) for item in value]
    raise TypeError(
        f"{type(value).__name__} is not serialisable; add an explicit rule rather than "
        "letting it fall through to a string repr"
    )


def _key(item: tuple[Any, Any]) -> str:
    """Sort mapping keys by their string form, so integer and string keys mix safely."""
    return str(item[0])


def _evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "name": evidence.name,
        "observed": _plain(evidence.observed),
        "payload": _plain(evidence.payload),
        "reason": evidence.reason,
        "status": str(evidence.status),
        "threshold": _plain(evidence.threshold),
        "warnings": list(evidence.warnings),
    }


def report_to_dict(report: ValidationReport) -> dict[str, Any]:
    """Convert a report into a plain dictionary with sorted keys throughout.

    Parameters
    ----------
    report : ValidationReport

    Returns
    -------
    dict
        Nested plain Python types only. The panel stays a **list** rather than a
        mapping, because its order is meaningful: it is the order the report
        renders the sections in, and a mapping sorted by name would scramble it.
    """
    return {
        "grid": _plain(report.grid),
        "panel": [_evidence_to_dict(entry) for entry in report.panel],
        "parameters": _plain(report.parameters),
        "provenance": _plain(report.provenance),
        "warnings": list(report.warnings),
    }


def report_to_json(report: ValidationReport) -> str:
    """Serialise a report to a deterministic JSON string.

    Returns
    -------
    str
        Two spaces of indentation, keys sorted, no non ASCII escaping surprises,
        and a trailing newline so the file is well formed for line based tools.
    """
    return (
        json.dumps(
            report_to_dict(report),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def write_json(report: ValidationReport, path: str | Path) -> Path:
    """Write the JSON serialisation to disk and return the path."""
    destination = Path(path)
    destination.write_text(report_to_json(report), encoding="utf-8")
    return destination
