"""Tests for the sentinel trading calendar.

The sentinel is the only calendar available before v0.7, so its failure modes
have to be typed and its realised session rate has to match the constant whose
derivation cites it. Both are asserted here rather than assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

import numpy as np
import pytest

from qvalid.adapters.calendars import WEEKDAYS_UTC_ID, weekdays_utc
from qvalid.core.constants import WEEKDAYS_PER_YEAR
from qvalid.exceptions import SchemaError


class TestConstruction:
    def test_only_weekdays_become_sessions(self) -> None:
        cal = weekdays_utc(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 15, tzinfo=UTC))
        stamps = (
            np.asarray(cal.session_close_ns)
            .astype("datetime64[ns]")
            .astype("datetime64[us]")
            .astype(datetime)
        )
        assert all(s.weekday() < 5 for s in stamps)
        assert cal.n_sessions == 10
        assert cal.calendar_id == WEEKDAYS_UTC_ID

    def test_closes_are_strictly_increasing(self) -> None:
        cal = weekdays_utc(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 6, 1, tzinfo=UTC))
        assert bool(np.all(np.diff(np.asarray(cal.session_close_ns)) > 0))

    def test_close_time_is_applied(self) -> None:
        cal = weekdays_utc(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 8, tzinfo=UTC),
            close=time(16, 30, tzinfo=UTC),
        )
        stamps = (
            np.asarray(cal.session_close_ns)
            .astype("datetime64[ns]")
            .astype("datetime64[us]")
            .astype(datetime)
        )
        assert {(s.hour, s.minute) for s in stamps} == {(16, 30)}

    def test_non_utc_close_is_converted_not_rejected(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        cal = weekdays_utc(
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 8, tzinfo=UTC),
            close=time(16, 0, tzinfo=eastern),
        )
        stamps = (
            np.asarray(cal.session_close_ns)
            .astype("datetime64[ns]")
            .astype("datetime64[us]")
            .astype(datetime)
        )
        assert {(s.hour, s.minute) for s in stamps} == {(21, 0)}

    def test_realised_rate_matches_the_declared_constant(self) -> None:
        cal = weekdays_utc(datetime(2000, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))
        assert cal.sessions_per_year() == pytest.approx(WEEKDAYS_PER_YEAR, rel=1e-3)

    def test_rate_is_not_the_equity_convention_of_252(self) -> None:
        """The sentinel counts holidays as sessions, which is why it is not 252."""
        cal = weekdays_utc(datetime(2000, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))
        assert cal.sessions_per_year() > 255.0


class TestTypedFailures:
    def test_naive_start_is_refused(self) -> None:
        with pytest.raises(SchemaError, match="timezone aware"):
            weekdays_utc(datetime(2024, 1, 1), datetime(2024, 2, 1, tzinfo=UTC))

    def test_naive_end_is_refused(self) -> None:
        with pytest.raises(SchemaError, match="timezone aware"):
            weekdays_utc(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1))

    def test_naive_close_is_refused(self) -> None:
        with pytest.raises(SchemaError, match="close must be timezone aware"):
            weekdays_utc(
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
                close=time(21, 0),
            )

    def test_inverted_interval_is_refused(self) -> None:
        with pytest.raises(SchemaError, match="must not precede"):
            weekdays_utc(datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC))

    def test_weekend_only_interval_is_refused(self) -> None:
        with pytest.raises(SchemaError, match="no weekday session"):
            weekdays_utc(datetime(2024, 1, 6, tzinfo=UTC), datetime(2024, 1, 7, tzinfo=UTC))

    def test_interval_holding_no_close_is_refused(self) -> None:
        """A weekday inside the interval is not enough; its close must be too."""
        with pytest.raises(SchemaError, match="no session close"):
            weekdays_utc(
                datetime(2024, 1, 8, 22, 0, tzinfo=UTC),
                datetime(2024, 1, 9, 6, 0, tzinfo=UTC),
            )
