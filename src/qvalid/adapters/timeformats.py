"""Work out which ``strftime`` pattern reads a column of stamps. See D066.

A person configuring this tool should not have to know ``strftime``. The
pattern is the one declaration in the mapping that is fully testable against the
file: either it parses the column or it does not, and that is a fact rather than
an inference.

**Every row, not the first.** Testing one stamp cannot separate ``%d/%m/%Y``
from ``%m/%d/%Y``, because ``05/03/2024`` is a valid date under both and means
different days. Testing the whole column usually can: one row with a day past
the twelfth eliminates the month first reading outright. So the check runs over
the column, and the ambiguity that survives is a real property of the file, not
a limitation of the check.

When two patterns both read every row and disagree about what the dates mean,
this reports both rather than choosing. Choosing would silently move a trade
from March to May, the grid would still be built, every statistic would still
be computed, and nothing downstream would look wrong. That is the failure this
project exists to remove, arriving through a date instead of a price.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

__all__ = ["CANDIDATE_FORMATS", "FormatMatch", "matching_formats"]

CANDIDATE_FORMATS: Final[tuple[str, ...]] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%m.%d.%Y %H:%M:%S",
    "%m.%d.%Y %H:%M",
    "%m.%d.%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%Y/%m/%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%m-%d-%Y %H:%M:%S",
    "%Y%m%d %H:%M:%S",
    "%Y%m%d",
)
"""Patterns worth trying, from the exports this project has seen.

Not exhaustive, and it does not need to be: the mapping still takes any pattern
the person writes, and this only saves them from writing one they could have
been shown. Ordered roughly by how often each turns up, since the first match
is what the form pre-selects when there is no ambiguity to report.

Both orderings of every ambiguous separator are here on purpose. The first
version of this list carried ``%m/%d/%Y`` but not ``%m.%d.%Y``, so a dotted
American date could not be detected as ambiguous: the check reported one
confident match because the rival was missing from the list it searched. An
ambiguity detector is only as honest as its candidates.
"""


@dataclass(frozen=True, slots=True)
class FormatMatch:
    """Which patterns read the column, and whether they agree.

    Attributes
    ----------
    parsing : tuple of str
        Patterns that read **every** stamp given.
    ambiguous : bool
        True when more than one pattern reads the whole column and they do not
        all produce the same instants. A file whose days never exceed twelve is
        the usual cause, and the honest answer is that the file cannot say.
    disagreement : str or None
        A stamp the surviving patterns read differently, with both readings, so
        the person can see what the choice costs rather than being told there
        is one.
    """

    parsing: tuple[str, ...]
    ambiguous: bool
    disagreement: str | None

    @property
    def only(self) -> str | None:
        """The single pattern that fits, or ``None`` if none or several do."""
        if self.ambiguous or len(self.parsing) != 1:
            return self.parsing[0] if len(self.parsing) == 1 else None
        return self.parsing[0]


def _read_all(pattern: str, samples: Sequence[str]) -> list[datetime] | None:
    """Parse every sample, or give up at the first one that will not read."""
    out: list[datetime] = []
    for text in samples:
        try:
            out.append(datetime.strptime(text.strip(), pattern))
        except (ValueError, TypeError):
            return None
    return out


def matching_formats(samples: Sequence[str]) -> FormatMatch:
    """Report the patterns that read a whole column of stamps.

    Parameters
    ----------
    samples : sequence of str
        Stamps as they appear in the file. Pass as many as are cheap: each one
        is another chance to eliminate a pattern, and the day first against
        month first question is settled by any single day past the twelfth.

    Returns
    -------
    FormatMatch
        Empty ``parsing`` when nothing fits, which is the answer for an export
        this module has not seen and is not a failure: the person writes the
        pattern themselves, exactly as before.

    Examples
    --------
    Unambiguous, because the twenty fifth cannot be a month::

        >>> matching_formats(["25.12.2024 09:30:00"]).only
        '%d.%m.%Y %H:%M:%S'

    Ambiguous, and reported as such rather than resolved::

        >>> matching_formats(["05.03.2024 09:30:00"]).ambiguous
        True
    """
    if not samples:
        return FormatMatch(parsing=(), ambiguous=False, disagreement=None)

    readings: dict[str, list[datetime]] = {}
    for pattern in CANDIDATE_FORMATS:
        parsed = _read_all(pattern, samples)
        if parsed is not None:
            readings[pattern] = parsed
    if not readings:
        return FormatMatch(parsing=(), ambiguous=False, disagreement=None)

    patterns = tuple(readings)
    first = readings[patterns[0]]
    disagreement: str | None = None
    for other in patterns[1:]:
        for index, (left, right) in enumerate(zip(first, readings[other], strict=True)):
            if left != right:
                disagreement = (
                    f"{samples[index].strip()!r} reads as {left:%Y-%m-%d} under "
                    f"{patterns[0]} and as {right:%Y-%m-%d} under {other}"
                )
                break
        if disagreement is not None:
            break
    return FormatMatch(
        parsing=patterns,
        ambiguous=disagreement is not None,
        disagreement=disagreement,
    )
