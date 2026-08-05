"""What "the example reproduces" means, stated once and used by both checkers.

The v1.0 criterion was written as byte for byte equality against a committed
reference. Measurement showed that is unachievable across dependency versions,
and D049 records the measurement: of 144 values in the report, **two** move
when numpy and scipy change version, by one to two units in the last place.
Both are reductions whose summation order the library chooses: a third moment,
and a survival function of the F distribution that goes through the platform's
``log`` and ``exp``.

Loosening a tolerance until a test passes is forbidden by ``04``, so the
criterion is derived instead of chosen. The report renders **six significant
figures**. Two runs whose values agree to better than that produce the same
report for the person reading it, and that is the property worth defending. The
bound here is ``1e-9`` relative, a thousand times tighter than the last digit
anyone sees, and five orders of magnitude above the drift measured across
versions. A failure therefore means either a real change or a drift large
enough to alter a printed number.

Byte for byte equality survives where it is achievable and still checked
there: ``test_report.py::TestByteForByte`` pins that two runs **in one
environment** produce identical bytes, which is the claim about the seed
governing everything, and it is unconditional.
"""

from __future__ import annotations

import math

__all__ = ["RELATIVE_TOLERANCE", "RENDERED_SIGNIFICANT_FIGURES", "moved_values"]

RENDERED_SIGNIFICANT_FIGURES = 6
"""What ``report/html.py`` prints. The criterion below is derived from it."""

RELATIVE_TOLERANCE = 1e-9
"""Three orders tighter than the last rendered digit. See D049."""


def _leaves(value: object, path: str = "") -> dict[str, object]:
    """Flatten a decoded report into ``path -> scalar``."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            out |= _leaves(item, f"{path}.{key}")
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out |= _leaves(item, f"{path}[{index}]")
        return out
    return {path: value}


def moved_values(produced: object, reference: object, *, ignore: tuple[str, ...] = ()) -> list[str]:
    """Every leaf that differs by more than the declared precision.

    Parameters
    ----------
    produced, reference : object
        Decoded reports.
    ignore : tuple of str, optional
        Leaf paths to skip, for the timestamp, which ``05`` allows to vary.

    Returns
    -------
    list of str
        One line per moved value, naming the path and both numbers, so a
        failure says which quantity changed rather than which file did.

    Notes
    -----
    Numbers are compared relatively; everything else, including strings, enums
    and the reason text of an absent section, is compared **exactly**. A regime
    label or a suppression reason that changed is not a rounding difference.
    """
    left, right = _leaves(produced), _leaves(reference)
    moved: list[str] = []
    for key in sorted(set(left) | set(right)):
        if key in ignore:
            continue
        a, b = left.get(key, _MISSING), right.get(key, _MISSING)
        if a is _MISSING or b is _MISSING:
            moved.append(f"{key}: only in {'produced' if b is _MISSING else 'reference'}")
            continue
        if isinstance(a, bool) or isinstance(b, bool) or not _both_numeric(a, b):
            if a != b:
                moved.append(f"{key}: {a!r} != {b!r}")
            continue
        if not math.isclose(float(a), float(b), rel_tol=RELATIVE_TOLERANCE, abs_tol=0.0):
            drift = abs(float(a) - float(b)) / abs(float(b)) if b else math.inf
            moved.append(f"{key}: {a!r} != {b!r} (relative {drift:.2e})")
    return moved


class _Missing:
    """Sentinel distinct from ``None``, which is a legitimate report value."""


_MISSING = _Missing()


def _both_numeric(a: object, b: object) -> bool:
    return isinstance(a, int | float) and isinstance(b, int | float)
