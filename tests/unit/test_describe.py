"""The grid read off a series' own stamps, and the statistics that follow. See D076.

The defect this version exists to remove was not found by a test. It was found
by watching an agent answer a question: given the cached reference series and
asked for its annualised volatility and its maximum drawdown, it read the array
through ``read_series``, found no tool that would compute anything, and wrote
``statistics.stdev`` times the square root of 252 into a scratch file.

That is the shape of everything below. The refusals are measured rather than
asserted from taste, the reconstruction of the level series is checked rather
than the label on the basis, and the difference between the right rate and 252
is pinned down as a number instead of described as a risk.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from qvalid.adapters.market import (
    GAP_BANDS,
    MAX_GAP_EXCESS_FRACTION,
    MarketSeries,
    observed_grid,
    parse_two_column_csv,
)
from qvalid.cli import app
from qvalid.contracts import Basis, Period, PeriodReturns
from qvalid.core.metrics import drawdown_profile, equity_curve
from qvalid.describe import describe_period_metrics
from qvalid.exceptions import InsufficientSampleError, SchemaError
from qvalid.mcp.protocol import handle
from qvalid.mcp.tools import MAX_ROWS

DAY_NS = 86_400 * 10**9
EPOCH = dt.date(1970, 1, 1)
DEMO = Path(__file__).resolve().parents[2] / "demo/indice-de-referencia.csv"


def days_of(start: dt.date, count: int) -> list[int]:
    """Weekday ordinals as day offsets from the epoch."""
    return [
        (start + dt.timedelta(days=index) - EPOCH).days
        for index in range(count)
        if (start + dt.timedelta(days=index)).weekday() < 5
    ]


def series_on(days: list[int], name: str = "X", levels: np.ndarray | None = None) -> MarketSeries:
    """A level series observed on exactly those days, drifting gently upward."""
    stamps = np.ascontiguousarray(np.asarray(sorted(set(days)), dtype=np.int64) * DAY_NS)
    if levels is None:
        levels = 100.0 * np.cumprod(1.0 + np.where(np.arange(stamps.size) % 3 == 0, -0.003, 0.004))
    return MarketSeries(
        timestamp_ns=stamps,
        values=np.ascontiguousarray(np.asarray(levels, dtype=np.float64)),
        series_id=name,
        n_missing=0,
    )


def demo_series() -> MarketSeries:
    return parse_two_column_csv(DEMO.read_bytes(), "REF")


def cache_with(folder: Path, path: Path, symbol: str = "REF", end: str = "2024-12-31") -> Path:
    root = folder / "cache"
    result = CliRunner().invoke(
        app,
        [
            "fetch",
            symbol,
            "--source",
            "file",
            "--file",
            str(path),
            "--start",
            "2022-01-03",
            "--end",
            end,
            "--cache",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    return root


def ask_tool(root: Path, name: str, **arguments: Any) -> dict[str, Any]:
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    ).encode()
    answer = handle(raw, root)
    assert answer is not None
    return json.loads(answer)["result"]


class TestTheGridIsReadOffTheStamps:
    def test_weekdays_read_as_daily_at_the_observed_rate(self) -> None:
        grid = observed_grid(series_on(days_of(dt.date(2022, 1, 3), 365 * 3)))
        assert grid.period is Period.DAILY
        assert grid.calendar_id == "OBSERVED:X"
        assert grid.gap_excess_fraction == 0.0

    def test_and_that_rate_is_not_252(self) -> None:
        """The whole reason this exists. A weekday series runs near 261 sessions
        a year, and scaling by the root of 252 understates the volatility by
        about nine parts in a thousand, which is invisible and wrong."""
        rate = observed_grid(demo_series()).periods_per_year
        assert rate == pytest.approx(260.99, abs=0.01)
        assert abs(rate - 252.0) > 5.0

    def test_the_rate_is_the_arithmetic_it_claims_to_be(self) -> None:
        """Computed here independently of the implementation: intervals divided
        by the span in Julian years, not observations divided by anything."""
        series = demo_series()
        span = int(series.timestamp_ns[-1] - series.timestamp_ns[0])
        years = span / (365.25 * DAY_NS)
        assert observed_grid(series).periods_per_year == pytest.approx(
            (series.n_observations - 1) / years
        )

    def test_a_weekly_series_takes_the_nominal_rate_and_says_so(self) -> None:
        """Weekly and monthly rates are exact by the definition of the calendar,
        so ``core.gridding`` uses the constants and this must agree with it. The
        identifier records which of the two paths produced the number."""
        grid = observed_grid(series_on(days_of(dt.date(2022, 1, 3), 365 * 3)[::5]))
        assert grid.period is Period.WEEKLY
        assert grid.calendar_id == "NOMINAL:WEEKLY"
        assert grid.periods_per_year == pytest.approx(365.25 / 7.0)

    def test_a_month_end_series_reads_as_monthly(self) -> None:
        by_month: dict[str, int] = {}
        for day in days_of(dt.date(2022, 1, 3), 365 * 3):
            by_month[(EPOCH + dt.timedelta(days=day)).strftime("%Y-%m")] = day
        grid = observed_grid(series_on(list(by_month.values())))
        assert grid.period is Period.MONTHLY
        assert grid.periods_per_year == 12.0


class TestWhatItRefuses:
    def test_a_real_exchange_closure_is_not_a_refusal(self) -> None:
        """Measured at 0.55% of the span for a week long closure, against a
        budget of 2%. The longest closure in the modern history of the New York
        Stock Exchange was four sessions, in September 2001."""
        days = days_of(dt.date(2022, 1, 3), 365 * 3)
        shut = {(dt.date(2023, 9, 11) - EPOCH).days + offset for offset in range(7)}
        grid = observed_grid(series_on([day for day in days if day not in shut]))
        assert grid.period is Period.DAILY
        assert 0.0 < grid.gap_excess_fraction < MAX_GAP_EXCESS_FRACTION

    def test_but_a_month_missing_is(self) -> None:
        """Measured at 2.74%. Annualising it would divide by a span the
        observations do not cover, and the drawdown would step over whatever
        happened inside the hole."""
        days = days_of(dt.date(2022, 1, 3), 365 * 3)
        lo = (dt.date(2023, 3, 1) - EPOCH).days
        hi = (dt.date(2023, 4, 1) - EPOCH).days
        with pytest.raises(SchemaError, match="of its span sits inside gaps"):
            observed_grid(series_on([day for day in days if not lo <= day <= hi]))

    def test_and_a_daily_series_spliced_onto_a_monthly_one_is(self) -> None:
        """The modal gap is one day and 58% of the span is not. A statistic that
        counted gaps rather than the span they cover would pass this."""
        days = days_of(dt.date(2022, 1, 3), 365 * 3)
        cut = (dt.date(2023, 1, 1) - EPOCH).days
        by_month: dict[str, int] = {}
        for day in days:
            by_month[(EPOCH + dt.timedelta(days=day)).strftime("%Y-%m")] = day
        mixed = [day for day in days if day < cut] + [
            day for month, day in by_month.items() if month >= "2023-01"
        ]
        with pytest.raises(SchemaError, match="58"):
            observed_grid(series_on(mixed))

    def test_a_hole_hides_from_the_statistic_that_counts_gaps(self) -> None:
        """The measurement that chose the criterion, kept as a test because the
        obvious statistic looked right. A six month hole leaves 99.8% of the
        gaps inside the daily band, a better score than a legitimate month end
        series manages, while covering a sixth of the sample with nothing."""
        days = days_of(dt.date(2022, 1, 3), 365 * 3)
        lo = (dt.date(2023, 3, 1) - EPOCH).days
        hi = (dt.date(2023, 9, 1) - EPOCH).days
        kept = np.asarray([day for day in days if not lo <= day <= hi])
        gaps = np.diff(kept)
        band_low, band_high = GAP_BANDS[Period.DAILY]
        in_band = float(((gaps >= band_low) & (gaps <= band_high)).mean())
        assert in_band > 0.99, "the discarded criterion would have passed this"
        with pytest.raises(SchemaError, match="of its span sits inside gaps"):
            observed_grid(series_on(kept.tolist()))

    def test_a_spacing_matching_no_rung_is_named(self) -> None:
        with pytest.raises(SchemaError, match="matches no grid"):
            observed_grid(series_on(days_of(dt.date(2022, 1, 3), 365 * 3)[::10]))

    def test_intraday_stamps_are_refused_rather_than_guessed_at(self) -> None:
        """They produce gaps of zero days. Nothing here knows how many bars a
        day of that series holds, so nothing here can annualise it."""
        stamps = np.ascontiguousarray(np.arange(5, dtype=np.int64) * 3600 * 10**9)
        hourly = MarketSeries(
            timestamp_ns=stamps,
            values=np.ascontiguousarray(np.full(5, 100.0)),
            series_id="H",
            n_missing=0,
        )
        with pytest.raises(SchemaError, match="matches no grid"):
            observed_grid(hourly)

    def test_two_observations_carry_no_evidence_of_regularity(self) -> None:
        with pytest.raises(InsufficientSampleError, match="fewer than three"):
            observed_grid(series_on(days_of(dt.date(2022, 1, 3), 365 * 3)[:2]))

    def test_a_level_at_or_below_zero_is_not_an_equity_path(self) -> None:
        days = days_of(dt.date(2022, 1, 3), 365)
        levels = np.linspace(50.0, -50.0, len(days))
        with pytest.raises(SchemaError, match="not an equity path"):
            series_on(days, levels=levels).to_period_returns()


class TestTheDeclaration:
    def test_the_equity_path_reconstructs_the_levels_exactly(self) -> None:
        """The invariant that makes the basis a fact rather than a label. Simple
        returns on a level series compose multiplicatively, so rebuilding the
        path from the declared basis has to give back the series it came from.
        """
        series = demo_series()
        rebuilt = equity_curve(series.to_period_returns())
        assert rebuilt == pytest.approx(series.values, rel=1e-12)

    def test_and_the_other_basis_would_have_changed_the_drawdown(self) -> None:
        """So the declaration is load bearing. Under ``FIXED_INITIAL`` the same
        returns are summed rather than compounded, and the drawdown that comes
        out is a different number with nothing on the screen to say so."""
        returns = demo_series().to_period_returns()
        assert returns.basis is Basis.CURRENT_EQUITY
        wrong = PeriodReturns(
            values=returns.values,
            period_end_ns=returns.period_end_ns,
            period=returns.period,
            periods_per_year=returns.periods_per_year,
            calendar_id=returns.calendar_id,
            basis=Basis.FIXED_INITIAL,
            initial_capital=returns.initial_capital,
            n_active=returns.n_active,
        )
        right = drawdown_profile(equity_curve(returns)).max_drawdown
        assert drawdown_profile(equity_curve(wrong)).max_drawdown != pytest.approx(right, rel=1e-6)

    def test_every_period_of_a_market_series_is_active(self) -> None:
        """So the dilution warning of ``02`` section 1.6 correctly stays quiet.
        It exists for capital parked between trades, and an index is never
        parked."""
        returns = demo_series().to_period_returns()
        assert returns.n_active == returns.n_periods
        assert describe_period_metrics(demo_series())["warnings"] == []

    def test_the_return_count_is_one_below_the_level_count(self) -> None:
        """The first observation is dropped rather than zeroed, per the rule
        D072 set for the trial matrix, and the rate is derived from the level
        stamps because those are the ones that span the returns."""
        series = demo_series()
        assert series.to_period_returns().n_periods == series.n_observations - 1


class TestTheDefectThisReplaces:
    def test_the_naive_scaling_is_wrong_by_a_measurable_amount(self) -> None:
        """Reconstructed exactly as it happened: standard deviation with
        ``ddof=1`` times the square root of 252. It is not a rounding
        difference, and no part of the answer said which rate had been used."""
        series = demo_series()
        levels = np.asarray(series.values, dtype=np.float64)
        returns = levels[1:] / levels[:-1] - 1.0
        improvised = float(returns.std(ddof=1)) * math.sqrt(252.0)

        payload = describe_period_metrics(series)
        computed = payload["volatility_annualised"]
        rate = payload["grid"]["periods_per_year"]

        assert improvised == pytest.approx(computed * math.sqrt(252.0 / rate))
        assert improvised == pytest.approx(0.2111, abs=0.0002)
        assert computed == pytest.approx(0.2148, abs=0.0002)

    def test_the_drawdown_it_got_right_is_still_right(self) -> None:
        """Worth pinning: the drawdown does not depend on the annualisation, so
        the improvised answer was correct there. Saying which half was wrong is
        the point of a record."""
        assert describe_period_metrics(demo_series())["drawdown"]["max_drawdown"] == pytest.approx(
            0.4398, abs=0.0002
        )

    def test_and_the_interval_it_never_printed_straddles_zero(self) -> None:
        """The number that settles the question, absent from the improvised
        answer entirely. See ``02`` section 1.3: a Sharpe ratio without an
        interval does not enter the report."""
        sharpe = describe_period_metrics(demo_series())["sharpe"]
        assert sharpe["ci_low"] < 0.0 < sharpe["ci_high"]


class TestOverTheWire:
    def test_the_tool_returns_the_grid_it_used(self, tmp_path: Path) -> None:
        root = cache_with(tmp_path, DEMO)
        payload = ask_tool(
            root,
            "describe_series",
            source="file",
            symbol="REF",
            start="2022-01-03",
            end="2024-12-31",
        )["structuredContent"]
        assert payload["grid"]["period"] == "DAILY"
        assert payload["grid"]["calendar_id"] == "OBSERVED:REF"
        assert payload["basis"] == "CURRENT_EQUITY"
        assert payload["volatility_annualised"] == pytest.approx(0.2148, abs=0.0002)

    def test_the_risk_free_rate_comes_back_with_the_answer(self, tmp_path: Path) -> None:
        """A Sharpe ratio whose risk free rate is not stated is not
        reproducible, and the caller supplied it rather than the tool."""
        root = cache_with(tmp_path, DEMO)
        payload = ask_tool(
            root,
            "describe_series",
            source="file",
            symbol="REF",
            start="2022-01-03",
            end="2024-12-31",
            risk_free_rate=0.05,
        )["structuredContent"]
        assert payload["sharpe"]["risk_free_rate_annual"] == 0.05
        assert payload["sharpe"]["annualised_sqrt_q"] < 0.0

    def test_an_irregular_series_is_refused_as_a_result_not_a_protocol_failure(
        self, tmp_path: Path
    ) -> None:
        """D075's distinction, applied to the new refusal: the request was
        understood and the answer is no."""
        days = days_of(dt.date(2022, 1, 3), 365 * 3)
        lo = (dt.date(2023, 3, 1) - EPOCH).days
        hi = (dt.date(2023, 6, 1) - EPOCH).days
        kept = [day for day in days if not lo <= day <= hi]
        source = tmp_path / "buraco.csv"
        lines = ["date,value"]
        level = 100.0
        for index, day in enumerate(kept):
            level *= 1.004 if index % 3 else 0.997
            lines.append(f"{(EPOCH + dt.timedelta(days=day)).isoformat()},{level:.4f}")
        source.write_text("\n".join(lines) + "\n")

        root = cache_with(tmp_path, source, symbol="HOLE")
        result = ask_tool(
            root,
            "describe_series",
            source="file",
            symbol="HOLE",
            start="2022-01-03",
            end="2024-12-31",
        )
        assert result["isError"] is True
        assert "sits inside gaps" in result["content"][0]["text"]

    def test_describing_has_no_row_cap_because_it_returns_no_rows(self, tmp_path: Path) -> None:
        """The complement of D075's honesty cap. ``read_series`` refuses above
        five thousand rows because a model handed the array summarises it and
        reports the summary as if it had read the series. Here the engine did
        the summarising, so there is nothing to cap."""
        days = days_of(dt.date(2000, 1, 3), 365 * 30)
        assert len(days) > MAX_ROWS
        source = tmp_path / "longa.csv"
        lines = ["date,value"]
        level = 100.0
        for index, day in enumerate(days):
            level *= 1.0004 if index % 3 else 0.9997
            lines.append(f"{(EPOCH + dt.timedelta(days=day)).isoformat()},{level:.4f}")
        source.write_text("\n".join(lines) + "\n")

        root = cache_with(tmp_path, source, symbol="LONG", end="2029-12-31")
        arguments = {
            "source": "file",
            "symbol": "LONG",
            "start": "2022-01-03",
            "end": "2029-12-31",
        }
        assert ask_tool(root, "read_series", **arguments)["isError"] is True
        described = ask_tool(root, "describe_series", **arguments)
        assert described["isError"] is False
        assert described["structuredContent"]["grid"]["n_periods"] == len(days) - 1


class TestTheCommand:
    def run(self, root: Path, *extra: str) -> Any:
        return CliRunner().invoke(
            app,
            [
                "describe",
                "REF",
                "--source",
                "file",
                "--start",
                "2022-01-03",
                "--end",
                "2024-12-31",
                "--cache",
                str(root),
                *extra,
            ],
        )

    def test_it_prints_the_rate_and_where_the_rate_came_from(self, tmp_path: Path) -> None:
        """Without those two lines the numbers below them are not
        reproducible, which is the failure the whole version is about."""
        result = self.run(cache_with(tmp_path, DEMO))
        assert result.exit_code == 0, result.stdout
        assert "260.99 periods per year, from OBSERVED:REF" in result.stdout
        assert "basis CURRENT_EQUITY" in result.stdout

    def test_the_sharpe_never_appears_without_its_interval(self, tmp_path: Path) -> None:
        result = self.run(cache_with(tmp_path, DEMO))
        for line in result.stdout.splitlines():
            if "Sharpe sqrt(q)" in line:
                assert "95% interval" in line
                break
        else:  # pragma: no cover - the line is always present for this fixture
            pytest.fail("no Sharpe line at all")

    def test_the_json_form_carries_the_same_numbers(self, tmp_path: Path) -> None:
        root = cache_with(tmp_path, DEMO)
        payload = json.loads(self.run(root, "--json").stdout)
        assert payload["volatility_annualised"] == pytest.approx(
            describe_period_metrics(demo_series())["volatility_annualised"]
        )

    def test_a_slice_that_was_never_fetched_is_refused_by_name(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                "describe",
                "GOLD",
                "--source",
                "fred",
                "--start",
                "2022-01-03",
                "--end",
                "2024-12-31",
                "--cache",
                str(cache_with(tmp_path, DEMO)),
            ],
        )
        assert result.exit_code == 2
        assert "qvalid fetch" in result.stdout + (result.stderr or "")
