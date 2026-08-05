"""Tests for the market series parsers and the real venue calendar.

No test here opens a socket. The split that makes that possible is deliberate:
a parser takes bytes and a fetcher takes a key, so everything except the three
lines that call ``urlopen`` is reachable from a test with a canned payload.

``TestTheRealCalendarClosesTheV01Prediction`` is the one worth reading. The
derivation of ``WEEKDAYS_PER_YEAR`` written in v0.1 predicted that using 252
with a sentinel calendar that counts holidays would misstate the annualisation
factor by about 1.8 per cent on the Sharpe ratio. With a real holiday list in
hand the prediction can finally be measured.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path

import numpy as np
import pytest
import yaml

from qvalid.adapters.cache import CacheKey, LocalCache
from qvalid.adapters.calendars import (
    WEEKDAYS_UTC_ID,
    VenueCalendarSpec,
    load_venue_calendar,
    weekdays_utc,
)
from qvalid.adapters.market import (
    FRED_API_KEY_ENV,
    FileFetcher,
    FredFetcher,
    MarketSeries,
    load_series,
    parse_fred_csv,
    parse_two_column_csv,
)
from qvalid.core.constants import WEEKDAYS_PER_YEAR
from qvalid.exceptions import SchemaError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CALENDAR = FIXTURES / "calendar_cme_equity.yaml"
FRED_PAYLOAD = b"DATE,DGS10\n2024-01-02,3.95\n2024-01-03,.\n2024-01-04,4.00\n"
STAMP = "2026-08-04T21:00:00Z"


class TestParsers:
    def test_fred_treats_a_dot_as_missing(self) -> None:
        series = parse_fred_csv(FRED_PAYLOAD, "DGS10")
        assert series.n_observations == 2
        assert series.n_missing == 1
        np.testing.assert_allclose(np.asarray(series.values), [3.95, 4.00])

    def test_the_missing_count_is_reported_not_absorbed(self) -> None:
        """A series with a fifth of its observations missing is a different object."""
        payload = b"DATE,X\n" + b"".join(
            f"2024-01-{day:02d},{'.' if day % 5 == 0 else '1.0'}\n".encode() for day in range(1, 21)
        )
        series = parse_fred_csv(payload, "X")
        assert series.n_missing == 4
        assert series.n_observations == 16

    def test_the_generic_parser_treats_an_empty_field_as_missing(self) -> None:
        series = parse_two_column_csv(b"date,x\n2024-01-02,1.5\n2024-01-03,\n", "X")
        assert series.n_observations == 1
        assert series.n_missing == 1

    def test_timestamps_come_out_in_utc_nanoseconds(self) -> None:
        series = parse_fred_csv(FRED_PAYLOAD, "DGS10")
        first = np.asarray(series.timestamp_ns)[0]
        assert datetime.fromtimestamp(int(first) / 1e9, tz=UTC).date().isoformat() == "2024-01-02"

    def test_a_single_column_file_is_refused(self) -> None:
        with pytest.raises(SchemaError, match="date column and a value column"):
            parse_two_column_csv(b"x\n1.0\n2.0\n", "X")

    def test_an_entirely_missing_series_is_refused(self) -> None:
        """An empty reference series would make every regime undefined silently."""
        with pytest.raises(SchemaError, match="no usable observation"):
            parse_fred_csv(b"DATE,X\n2024-01-02,.\n2024-01-03,.\n", "X")

    def test_unparseable_dates_are_refused(self) -> None:
        with pytest.raises(SchemaError, match=r"does not parse|do not parse"):
            parse_two_column_csv(b"date,x\n2024-01-02,1.0\nnot a date,2.0\n", "X")

    def test_a_blank_date_is_refused_rather_than_dropped(self) -> None:
        """pandas turns an empty date into NaT without raising; a partial parse loses rows."""
        with pytest.raises(SchemaError, match=r"dates of .* do not parse"):
            parse_two_column_csv(b"date,x\n2024-01-02,1.0\n,2.0\n", "X")

    def test_the_contract_refuses_mismatched_lengths(self) -> None:
        with pytest.raises(SchemaError, match="same length"):
            MarketSeries(
                timestamp_ns=np.array([1, 2], dtype=np.int64),
                values=np.array([1.0], dtype=np.float64),
                series_id="X",
                n_missing=0,
            )

    def test_the_contract_refuses_unordered_timestamps(self) -> None:
        with pytest.raises(SchemaError, match="strictly increasing"):
            MarketSeries(
                timestamp_ns=np.array([2, 1], dtype=np.int64),
                values=np.array([1.0, 2.0], dtype=np.float64),
                series_id="X",
                n_missing=0,
            )


class TestFetchers:
    def test_the_fred_key_is_refused_when_absent(self, monkeypatch) -> None:
        monkeypatch.delenv(FRED_API_KEY_ENV, raising=False)
        with pytest.raises(SchemaError, match="no FRED API key"):
            FredFetcher()

    def test_the_key_is_read_from_the_environment(self, monkeypatch) -> None:
        """``04``: a key in the versioned config would travel with the provenance."""
        monkeypatch.setenv(FRED_API_KEY_ENV, "secret")
        url = FredFetcher().url_for(
            CacheKey(source="fred", symbol="DGS10", start="2024-01-01", end="2024-12-31")
        )
        assert "series_id=DGS10" in url
        assert "observation_start=2024-01-01" in url
        assert url.startswith("https://api.stlouisfed.org/")

    def test_fred_costs_nothing(self, monkeypatch) -> None:
        monkeypatch.setenv(FRED_API_KEY_ENV, "secret")
        assert FredFetcher().estimated_cost == 0.0

    def test_a_local_file_goes_through_the_same_cache(self, tmp_path: Path) -> None:
        """Data already on disk still gets a manifest line, so its origin is recorded."""
        source = tmp_path / "series.csv"
        source.write_bytes(FRED_PAYLOAD)
        cache = LocalCache(tmp_path / "data")
        key = CacheKey(source="local", symbol="DGS10", start="2024-01-01", end="2024-12-31")
        series = load_series(
            cache,
            key,
            FileFetcher(str(source), cost=9.0),
            parser=parse_fred_csv,
            recorded_at=STAMP,
        )
        assert series.n_observations == 2
        assert cache.total_cost() == 9.0
        assert cache.manifest()[0]["source"] == "local"

    def test_a_missing_local_file_is_refused(self, tmp_path: Path) -> None:
        fetcher = FileFetcher(str(tmp_path / "absent.csv"))
        with pytest.raises(SchemaError, match="no file at"):
            fetcher.fetch(CacheKey(source="local", symbol="X", start="a", end="b"))

    def test_the_second_load_does_not_touch_the_source(self, tmp_path: Path) -> None:
        source = tmp_path / "series.csv"
        source.write_bytes(FRED_PAYLOAD)
        cache = LocalCache(tmp_path / "data")
        key = CacheKey(source="local", symbol="DGS10", start="2024-01-01", end="2024-12-31")
        load_series(cache, key, FileFetcher(str(source)), recorded_at=STAMP)
        source.unlink()
        again = load_series(cache, key, FileFetcher(str(source)), recorded_at=STAMP)
        assert again.n_observations == 2
        assert cache.downloads() == 1


class TestTheRealCalendarClosesTheV01Prediction:
    """The derivation written in v0.1, finally measurable."""

    START = datetime(2024, 1, 1, tzinfo=UTC)
    END = datetime(2025, 12, 31, tzinfo=UTC)

    def test_the_real_calendar_has_fewer_sessions_than_the_sentinel(self) -> None:
        real = load_venue_calendar(CALENDAR).materialise(self.START, self.END)
        sentinel = weekdays_utc(self.START, self.END)
        assert real.n_sessions < sentinel.n_sessions
        assert real.calendar_id == "CME_EQUITY_INDEX"
        assert sentinel.calendar_id == WEEKDAYS_UTC_ID

    def test_the_real_rate_lands_near_the_252_convention(self) -> None:
        real = load_venue_calendar(CALENDAR).materialise(self.START, self.END)
        assert 249.0 < real.sessions_per_year() < 254.0

    def test_the_sentinel_overstates_the_rate_by_the_predicted_amount(self) -> None:
        """``WEEKDAYS_PER_YEAR`` predicted about 1.8 per cent on the Sharpe ratio."""
        real = load_venue_calendar(CALENDAR).materialise(self.START, self.END)
        sentinel = weekdays_utc(self.START, self.END)
        assert sentinel.sessions_per_year() == pytest.approx(WEEKDAYS_PER_YEAR, rel=0.01)
        effect = (real.sessions_per_year() / sentinel.sessions_per_year()) ** 0.5 - 1.0
        assert -0.025 < effect < -0.012

    def test_half_days_stay_sessions_with_an_earlier_close(self) -> None:
        """A half day is a session; removing it would understate the count."""
        real = load_venue_calendar(CALENDAR).materialise(self.START, self.END)
        stamps = (
            np.asarray(real.session_close_ns)
            .astype("datetime64[ns]")
            .astype("datetime64[us]")
            .astype(datetime)
        )
        hours = {(s.hour, s.minute) for s in stamps}
        assert len(hours) > 1


class TestVenueCalendarValidation:
    def _payload(self, **overrides):
        payload = yaml.safe_load(CALENDAR.read_text(encoding="utf-8"))
        payload.update(overrides)
        return payload

    def _write(self, tmp_path: Path, payload) -> Path:
        path = tmp_path / "cal.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    def test_the_fixture_loads(self) -> None:
        spec = load_venue_calendar(CALENDAR)
        assert spec.timezone == "America/Chicago"
        assert spec.close == time(16, 0)
        assert len(spec.holidays) == 20

    def test_an_unknown_timezone_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="unknown IANA timezone"):
            load_venue_calendar(self._write(tmp_path, self._payload(timezone="Mars/Base")))

    def test_an_empty_weekmask_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="at least one weekday"):
            load_venue_calendar(self._write(tmp_path, self._payload(weekmask=[])))

    def test_an_out_of_range_weekmask_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match=r"\[0, 6\]"):
            load_venue_calendar(self._write(tmp_path, self._payload(weekmask=[0, 9])))

    def test_an_unknown_key_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match=r"[Ee]xtra"):
            load_venue_calendar(self._write(tmp_path, self._payload(sessions=3)))

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="not found"):
            load_venue_calendar(tmp_path / "absent.yaml")

    def test_malformed_yaml_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("holidays: [unclosed\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="not valid YAML"):
            load_venue_calendar(path)

    def test_a_non_mapping_document_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="must be a mapping"):
            load_venue_calendar(path)

    def test_naive_bounds_are_refused(self) -> None:
        spec = load_venue_calendar(CALENDAR)
        with pytest.raises(SchemaError, match="timezone aware"):
            spec.materialise(datetime(2024, 1, 1), datetime(2024, 2, 1, tzinfo=UTC))

    def test_an_inverted_interval_is_refused(self) -> None:
        spec = load_venue_calendar(CALENDAR)
        with pytest.raises(SchemaError, match="must not precede"):
            spec.materialise(datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC))

    def test_an_interval_of_only_holidays_is_refused(self) -> None:
        spec = load_venue_calendar(CALENDAR)
        with pytest.raises(SchemaError, match="no session between"):
            spec.materialise(
                datetime(2024, 12, 25, tzinfo=UTC), datetime(2024, 12, 25, 12, tzinfo=UTC)
            )

    def test_a_custom_weekmask_is_honoured(self) -> None:
        spec = VenueCalendarSpec(
            calendar_id="CRYPTO",
            timezone="UTC",
            close=time(0, 0),
            weekmask=[0, 1, 2, 3, 4, 5, 6],
        )
        calendar = spec.materialise(
            datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 31, tzinfo=UTC)
        )
        assert calendar.n_sessions == 31

    def test_holidays_are_removed_and_early_closes_are_not(self) -> None:
        spec = VenueCalendarSpec(
            calendar_id="TEST",
            timezone="UTC",
            close=time(21, 0),
            holidays=[datetime(2024, 1, 3).date()],
            early_closes={datetime(2024, 1, 4).date(): time(18, 0)},
        )
        calendar = spec.materialise(
            datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 5, 23, tzinfo=UTC)
        )
        stamps = (
            np.asarray(calendar.session_close_ns)
            .astype("datetime64[ns]")
            .astype("datetime64[us]")
            .astype(datetime)
        )
        days = {s.day for s in stamps}
        assert 3 not in days
        assert 4 in days
        assert next(s for s in stamps if s.day == 4).hour == 18
