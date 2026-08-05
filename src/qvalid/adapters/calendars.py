"""Materialisation of :class:`~qvalid.contracts.TradingCalendar` objects.

Lives in the adapter layer because a calendar is external information: it comes
from the symbology map, not from the trade log. ``core`` receives the
materialised object as a typed argument and never builds one, per the
dependency rule of ``01``.

The v0.1 sentinel is :func:`weekdays_utc`, which treats every weekday as a
session. It is a declared default, not a silent one: the ``calendar_id`` it
stamps travels into the ``ValidationReport``, so a reader can tell that the
holiday schedule of the venue was not applied.

From v0.7 a real venue calendar is available through :class:`VenueCalendarSpec`,
declared in a versioned YAML file with holidays, the regular close and any half
days. The choice of a file over a calendar library follows D016: the data that
changes the result lives beside the code and no dependency enters for it. The
cost is that the holiday list needs maintenance, and a missing holiday leaves a
session the venue did not have, inflating the period count and deflating every
annualised statistic by the square root of the ratio.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from qvalid.contracts import NANOS_PER_SECOND, TradingCalendar, to_utc_nanos
from qvalid.exceptions import SchemaError

__all__ = [
    "WEEKDAYS_UTC_ID",
    "VenueCalendarSpec",
    "load_venue_calendar",
    "weekdays_utc",
]

WEEKDAYS_UTC_ID = "WEEKDAYS_UTC"
"""Identifier stamped on the sentinel calendar. See ``01``."""

_MONDAY_EPOCH_DAY_RESIDUE = 4
"""1970-01-01 was a Thursday, so days that are Mondays satisfy ``day % 7 == 4``."""

_NANOS_PER_DAY = 86_400 * NANOS_PER_SECOND


def weekdays_utc(
    start: datetime,
    end: datetime,
    *,
    close: time = time(21, 0, tzinfo=UTC),
) -> TradingCalendar:
    """Build the ``WEEKDAYS_UTC`` sentinel calendar.

    Parameters
    ----------
    start, end : datetime.datetime
        Timezone aware bounds of the calendar, inclusive. Sessions are emitted
        for every Monday to Friday whose close falls inside the interval.
    close : datetime.time, optional
        Session close, timezone aware, applied to every weekday. Defaults to
        21:00 UTC, which is the settlement hour of the CME equity index
        complex and therefore a defensible sentinel for futures logs.

    Returns
    -------
    TradingCalendar
        Calendar identified as ``WEEKDAYS_UTC``.

    Raises
    ------
    SchemaError
        If a bound is naive, if the interval is empty, or if ``close`` carries
        no timezone. ``01`` forbids naive timestamps at every boundary.

    Notes
    -----
    This calendar includes exchange holidays as sessions, which is why
    :data:`~qvalid.core.constants.WEEKDAYS_PER_YEAR` is 260.89 rather than 252.
    Substituting a real venue calendar in v0.7 lowers the session count and
    raises the annualisation factor; the direction of that revision is known in
    advance and is recorded in the constant's derivation.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise SchemaError(f"calendar bounds must be timezone aware, got {start!r} and {end!r}")
    if close.tzinfo is None:
        raise SchemaError(f"close must be timezone aware, got {close!r}")
    if end < start:
        raise SchemaError(f"end must not precede start, got start={start!r}, end={end!r}")

    start_day = start.astimezone(UTC).date()
    end_day = end.astimezone(UTC).date()
    days = np.arange(
        np.datetime64(start_day, "D"),
        np.datetime64(end_day, "D") + np.timedelta64(1, "D"),
        dtype="datetime64[D]",
    )
    weekday = (days.astype(np.int64) - _MONDAY_EPOCH_DAY_RESIDUE) % 7
    sessions = days[weekday < 5]
    if sessions.size == 0:
        raise SchemaError(f"no weekday session between {start_day} and {end_day}")

    close_offset_ns = to_utc_nanos(
        [datetime.combine(sessions[0].astype("datetime64[D]").item(), close)]
    )[0] - int(sessions[0].astype("datetime64[ns]").astype(np.int64))
    close_ns = sessions.astype("datetime64[ns]").astype(np.int64) + close_offset_ns

    lo = to_utc_nanos([start])[0]
    hi = to_utc_nanos([end])[0]
    close_ns = close_ns[(close_ns >= lo) & (close_ns <= hi)]
    if close_ns.size == 0:
        raise SchemaError(
            f"no session close between {start.isoformat()} and {end.isoformat()}; "
            f"widen the interval or move the close time from {close.isoformat()}"
        )
    return TradingCalendar(calendar_id=WEEKDAYS_UTC_ID, session_close_ns=close_ns)


class VenueCalendarSpec(BaseModel):
    """A real venue calendar, declared in a versioned YAML file.

    Attributes
    ----------
    calendar_id : str
        Enters the ``ValidationReport``, so switching calendars is visible in
        the report rather than buried in a config diff.
    timezone : str
        IANA zone the close times are expressed in. Sessions are converted to
        UTC on materialisation, per ``01``.
    close : datetime.time
        Regular session close, naive, interpreted in ``timezone``.
    early_closes : mapping of date to time
        Half days. Kept separate from holidays because a half day **is** a
        session and removing it would understate the session count.
    holidays : sequence of date
        Full closures. Every one removed from the grid changes
        ``periods_per_year``, which is why the file is versioned.
    weekmask : sequence of int
        Weekdays that can be sessions, Monday zero. Defaults to Monday to
        Friday. Present for venues that trade on other schedules.

    Notes
    -----
    Chosen over a calendar library for the reason D016 gives: the data that
    changes the result lives versioned beside the code, and no new dependency
    enters for it. **Limitation, declared rather than discovered.** The holiday
    list has to be maintained. A missing holiday leaves a session in the grid
    that the venue did not have, which inflates the period count and deflates
    every annualised statistic by the square root of the ratio. The
    ``calendar_id`` is what lets a reader tell which list produced a number.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    calendar_id: str
    timezone: str
    close: time
    holidays: Sequence[date] = ()
    early_closes: Mapping[date, time] = Field(default_factory=dict)
    weekmask: Sequence[int] = (0, 1, 2, 3, 4)

    @field_validator("timezone")
    @classmethod
    def _zone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {value!r}: {exc}") from exc
        return value

    @field_validator("weekmask")
    @classmethod
    def _weekmask_is_sane(cls, value: Sequence[int]) -> Sequence[int]:
        if not value:
            raise ValueError("weekmask must name at least one weekday")
        if any(day < 0 or day > 6 for day in value):
            raise ValueError(f"weekmask entries must lie in [0, 6], got {list(value)}")
        return value

    def materialise(self, start: datetime, end: datetime) -> TradingCalendar:
        """Build the ``TradingCalendar`` over an interval.

        Parameters
        ----------
        start, end : datetime.datetime
            Timezone aware bounds, inclusive.

        Returns
        -------
        TradingCalendar

        Raises
        ------
        SchemaError
            Naive bounds, an inverted interval, or an interval containing no
            session at all.
        """
        if start.tzinfo is None or end.tzinfo is None:
            raise SchemaError(f"calendar bounds must be timezone aware, got {start!r} and {end!r}")
        if end < start:
            raise SchemaError(f"end must not precede start, got {start!r} and {end!r}")

        zone = ZoneInfo(self.timezone)
        holidays = set(self.holidays)
        allowed = set(self.weekmask)
        closes: list[int] = []
        current = start.astimezone(UTC).date()
        last = end.astimezone(UTC).date()
        while current <= last:
            if current.weekday() in allowed and current not in holidays:
                session_close = self.early_closes.get(current, self.close)
                stamp = datetime.combine(current, session_close, tzinfo=zone)
                closes.append(to_utc_nanos([stamp])[0])
            current += timedelta(days=1)

        lo, hi = to_utc_nanos([start])[0], to_utc_nanos([end])[0]
        inside = [value for value in closes if lo <= value <= hi]
        if not inside:
            raise SchemaError(
                f"calendar {self.calendar_id!r} has no session between "
                f"{start.isoformat()} and {end.isoformat()}"
            )
        return TradingCalendar(
            calendar_id=self.calendar_id,
            session_close_ns=np.ascontiguousarray(sorted(inside), dtype=np.int64),
        )


def load_venue_calendar(path: str | Path) -> VenueCalendarSpec:
    """Load and validate a venue calendar from YAML.

    Raises
    ------
    SchemaError
        Missing file, malformed YAML, or any field failing validation.

    Examples
    --------
    ::

        calendar_id: CME_EQUITY_INDEX
        timezone: America/Chicago
        close: "16:00:00"
        holidays: [2024-01-01, 2024-12-25]
        early_closes:
          2024-07-03: "12:00:00"
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SchemaError(f"venue calendar not found at {file_path}")
    try:
        raw: Any = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchemaError(f"venue calendar at {file_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError(
            f"venue calendar at {file_path} must be a mapping, got {type(raw).__name__}"
        )
    try:
        return VenueCalendarSpec.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"venue calendar at {file_path} is invalid: {exc}") from exc
