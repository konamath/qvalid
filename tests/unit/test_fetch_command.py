"""The cache finally has a caller. See D074.

Measured before this version, the fifth time the same shape appeared:

    linhas em adapters/cache.py + adapters/market.py   684
    modulos de src fora deles que chamam load_series     0
    modulos de src fora deles que chamam LocalCache      0
    comandos qvalid que tocam o cache                    0

``LocalCache`` with its manifest, hash verification and cost accounting, the
``Fetcher`` protocol, the FRED adapter and the parsers were all built in v0.7,
tested, and never once called from outside their own modules.

Everything here runs offline, which is what D033 bought by putting the network
behind an injectable protocol: ``--source file`` is a real feature for data you
already have on disk, and it happens to make the command testable without a
socket.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from qvalid.adapters.market import MarketSeries
from qvalid.cli import app
from qvalid.exceptions import InsufficientSampleError, SchemaError
from qvalid.pipeline import run_validation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DAY_NS = 86_400 * 10**9


def source_csv(folder: Path, rows: int = 300) -> Path:
    """A two column level series, the shape every free index source has."""
    lines = ["date,value"]
    level = 4000.0
    for index in range(rows):
        day = dt.date(2022, 1, 3) + dt.timedelta(days=index)
        if day.weekday() < 5:
            level *= 1.0 + (0.004 if index % 3 else -0.003)
            lines.append(f"{day.isoformat()},{level:.2f}")
    path = folder / "levels.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def grid_closes(folder: Path, log: Path) -> np.ndarray:
    """The run's own period closes, taken from the engine rather than guessed."""
    from qvalid.adapters.calendars import weekdays_utc
    from qvalid.adapters.symbology import load_symbology
    from qvalid.adapters.tradelog import load_mapping, read_trade_log_csv
    from qvalid.contracts import Basis
    from qvalid.core.gridding import select_grid

    imported = read_trade_log_csv(
        log,
        load_mapping(folder / "mapping_generic.yaml"),
        load_symbology(folder / "symbology.yaml"),
    )
    exits = np.asarray(imported.log.exit_ns)
    span = dt.timedelta(days=5)
    calendar = weekdays_utc(
        dt.datetime.fromtimestamp(int(exits.min()) / 1e9, tz=dt.UTC) - span,
        dt.datetime.fromtimestamp(int(exits.max()) / 1e9, tz=dt.UTC) + span,
    )
    returns = select_grid(
        imported.log, calendar, basis=Basis.FIXED_INITIAL, initial_capital=100000.0
    ).returns
    return np.ascontiguousarray(returns.period_end_ns)


def fetch(folder: Path, *extra: str) -> tuple[int, str]:
    result = CliRunner().invoke(
        app,
        [
            "fetch",
            "SP500",
            "--source",
            "file",
            "--file",
            str(source_csv(folder)),
            "--start",
            "2022-01-03",
            "--end",
            "2022-10-29",
            "--cache",
            str(folder / "cache"),
            *extra,
        ],
    )
    return result.exit_code, result.stdout


class TestTheCacheDoesWhatItPromised:
    def test_the_second_request_does_not_fetch_again(self, tmp_path: Path) -> None:
        """D033's whole claim, and until now nothing exercised it from a command."""
        assert fetch(tmp_path)[0] == 0
        second = fetch(tmp_path)[1]
        assert "already cached" in second
        assert "1 download(s)" in second

    def test_and_the_first_request_says_it_downloaded(self, tmp_path: Path) -> None:
        """The status line used to lie. ``downloads`` is a method, the command
        compared two bound method objects, and every run reported a hit."""
        first = fetch(tmp_path)[1]
        assert "downloaded" in first
        assert "already cached" not in first

    def test_every_request_is_recorded_including_the_hits(self, tmp_path: Path) -> None:
        """Omitting the hits would make the manifest say a slice was fetched
        once when it was used forty times. See D033."""
        fetch(tmp_path)
        fetch(tmp_path)
        manifest = (tmp_path / "cache" / "manifest.jsonl").read_text().splitlines()
        assert len(manifest) == 2
        assert [json.loads(line)["downloaded"] for line in manifest] == [True, False]

    def test_the_estimated_cost_is_reported(self, tmp_path: Path) -> None:
        """`03` asks for the cost of a paid source to be visible before it grows."""
        assert "of estimated cost" in fetch(tmp_path)[1]


class TestTheRefusals:
    def test_an_unknown_source(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                "fetch",
                "X",
                "--source",
                "bloomberg",
                "--start",
                "a",
                "--end",
                "b",
                "--cache",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 2

    def test_the_three_ways_a_file_source_can_be_wrong_say_which(self, tmp_path: Path) -> None:
        """One message used to cover all three, and the one that actually
        happens is a path that does not exist. It happened on the first real
        attempt, against a placeholder path written into a runnable looking
        command. Saying which is the difference between fixing it and guessing.
        """

        def run(*extra: str) -> str:
            result = CliRunner().invoke(
                app,
                [
                    "fetch",
                    "X",
                    "--source",
                    "file",
                    "--start",
                    "a",
                    "--end",
                    "b",
                    "--cache",
                    str(tmp_path / "cache"),
                    *extra,
                ],
            )
            assert result.exit_code == 2
            return (result.stdout or "") + (getattr(result, "stderr", "") or "")

        assert "needs --file" in run()
        assert "no file at" in run("--file", str(tmp_path / "absent.csv"))
        assert "is a directory" in run("--file", str(tmp_path))

    def test_a_file_source_without_a_file(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app,
            [
                "fetch",
                "X",
                "--source",
                "file",
                "--start",
                "a",
                "--end",
                "b",
                "--cache",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 2


class TestLevelsToReturns:
    def series(self, values: list[float]) -> MarketSeries:
        return MarketSeries(
            timestamp_ns=np.arange(len(values), dtype=np.int64) * DAY_NS,
            values=np.asarray(values, dtype=np.float64),
            series_id="X",
            n_missing=0,
        )

    def test_it_is_the_simple_return(self) -> None:
        result = self.series([100.0, 101.0, 99.99]).to_returns()
        assert result.values == pytest.approx([0.01, -0.01])

    def test_the_first_observation_is_dropped_rather_than_set_to_zero(self) -> None:
        """A zero would say the market did not move on a day it was not
        observed, which is the error D072 refused in the trial matrix."""
        result = self.series([100.0, 101.0, 102.0]).to_returns()
        assert result.n_observations == 2
        assert result.timestamp_ns[0] == DAY_NS

    def test_one_level_cannot_make_a_return(self) -> None:
        with pytest.raises(InsufficientSampleError):
            self.series([100.0]).to_returns()

    def test_a_zero_level_is_refused_rather_than_dividing(self) -> None:
        with pytest.raises(SchemaError, match="zero level"):
            self.series([100.0, 0.0, 50.0]).to_returns()

    def test_the_identifier_says_what_happened_to_it(self) -> None:
        assert self.series([1.0, 2.0]).to_returns().series_id == "X:returns"


class TestTheWrittenFileIsTheOneTheRunReads:
    """The payoff: fetch produces the reference series the regimes section of
    ``02`` section 4 needs, and that section has been reachable only by anyone
    willing to write the file by hand."""

    def test_the_header_and_shape_match_what_load_reference_expects(self, tmp_path: Path) -> None:
        fetch(tmp_path, "--as-returns", "--out", str(tmp_path / "ref.csv"))
        lines = (tmp_path / "ref.csv").read_text().splitlines()
        assert lines[0] == "period_end,ret"
        assert "+00:00," in lines[1], "timezone aware, per D032"

    def test_a_reference_built_this_way_makes_the_regimes_section_run(self, tmp_path: Path) -> None:
        """Built on the run's own grid closes, because D032 aligns by exact
        timestamp and refuses a source whose calendar differs. That refusal is
        the feature; this shows the other side of it."""
        for name in ("mapping_generic.yaml", "symbology.yaml"):
            (tmp_path / name).write_text((FIXTURES / name).read_text())
        settings = {
            "symbology_path": "symbology.yaml",
            "mapping_path": "mapping_generic.yaml",
            "initial_capital": 100000.0,
            "basis": "FIXED_INITIAL",
            "seed": 20260806,
            "risk_free_rate": 0.0,
            "n_paths": 100,
        }
        config = tmp_path / "run.yaml"
        config.write_text(yaml.safe_dump(settings))
        log = FIXTURES / "trades_winner.csv"

        stamps = grid_closes(tmp_path, log)
        series = MarketSeries(
            timestamp_ns=stamps,
            values=np.ascontiguousarray(np.random.default_rng(11).normal(0.0, 0.01, stamps.size)),
            series_id="REF",
            n_missing=0,
        )
        (tmp_path / "ref.csv").write_text(series.to_reference_csv())
        config.write_text(yaml.safe_dump({**settings, "reference_path": "ref.csv"}))

        entry = run_validation(log, config).report.entry("regimes")
        assert entry.status.value == "RAN", entry.reason

    def test_a_reference_on_another_calendar_is_refused_by_name(self, tmp_path: Path) -> None:
        """D032 again: a series shifted by one session would relabel every
        period, and a positional read would hand the regime grid something that
        looks aligned and is not."""
        for name in ("mapping_generic.yaml", "symbology.yaml"):
            (tmp_path / name).write_text((FIXTURES / name).read_text())
        log = FIXTURES / "trades_winner.csv"
        stamps = grid_closes(tmp_path, log)
        shifted = MarketSeries(
            timestamp_ns=np.ascontiguousarray(stamps + DAY_NS),
            values=np.ascontiguousarray(np.zeros(stamps.size)),
            series_id="REF",
            n_missing=0,
        )
        (tmp_path / "ref.csv").write_text(shifted.to_reference_csv())
        config = tmp_path / "run.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "symbology_path": "symbology.yaml",
                    "mapping_path": "mapping_generic.yaml",
                    "initial_capital": 100000.0,
                    "basis": "FIXED_INITIAL",
                    "seed": 20260806,
                    "risk_free_rate": 0.0,
                    "n_paths": 100,
                    "reference_path": "ref.csv",
                }
            )
        )
        entry = run_validation(log, config).report.entry("regimes")
        assert entry.status.value == "FAILED"
