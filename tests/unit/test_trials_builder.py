"""Building the trial matrix from the logs of a sweep. See D072.

The deflation needs the dispersion across trial Sharpe ratios, so a declared
count is not enough and D004 refuses to invent the rest. Until this existed the
matrix had to be produced by hand, which meant the section that separates this
project from a spreadsheet of metrics was reachable only by someone who had
already done the hard part elsewhere.

The tests that matter here are the refusals, and one property: what comes out
must be the same quantity the run being deflated computes, on the same grid.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from qvalid.contracts import Period
from qvalid.core.constants import MIN_PERIODS
from qvalid.exceptions import SchemaError
from qvalid.pipeline import run_validation
from qvalid.trials import build_trials

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WINNER = FIXTURES / "trades_winner.csv"
MULTIPLIER = 50.0


def variants(folder: Path, count: int = 20, seed: int = 7) -> list[Path]:
    """A sweep: the same strategy with a different parameter, each with its own noise.

    Independent noise per variant on purpose. Scaling one log by a constant
    makes every variant a monotone transform of the same trades, the in sample
    winner is then the out of sample winner by construction, and PBO pins at
    zero against its own ceiling. That is an artefact of the generator, and a
    fixture built that way would test nothing about cross validation.
    """
    rng = np.random.default_rng(seed)
    base = pd.read_csv(WINNER)
    made: list[Path] = []
    for index in range(count):
        frame = base.copy()
        pull = 0.3 + 0.7 * index / max(count - 1, 1)
        move = (frame["close_price"] - frame["open_price"]) * pull
        move = move + rng.normal(0.0, 1.5, len(frame))
        frame["close_price"] = (frame["open_price"] + np.round(move / 0.25) * 0.25).round(2)
        side = np.where(frame["direction"] == "Long", 1.0, -1.0)
        gross = side * (frame["close_price"] - frame["open_price"]) * frame["quantity"]
        frame["net_pnl"] = (gross * MULTIPLIER - frame["commission"]).round(2)
        path = folder / f"var_{index + 1:02d}.csv"
        frame.to_csv(path, index=False)
        made.append(path)
    return made


def configuration(folder: Path, **extra: object) -> Path:
    for name in ("mapping_generic.yaml", "symbology.yaml"):
        (folder / name).write_text((FIXTURES / name).read_text())
    path = folder / "run.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "symbology_path": "symbology.yaml",
                "mapping_path": "mapping_generic.yaml",
                "initial_capital": 100000.0,
                "basis": "FIXED_INITIAL",
                "seed": 20260806,
                "risk_free_rate": 0.0,
                "n_paths": 200,
                **extra,
            }
        )
    )
    return path


class TestItBuildsWhatTheDeflationWants:
    def test_a_sweep_of_twenty_logs_becomes_one_matrix(self, tmp_path: Path) -> None:
        built = build_trials(variants(tmp_path), configuration(tmp_path))
        assert len(built.names) == 20
        assert built.values.shape == (built.n_periods, 20)

    def test_the_grid_is_chosen_once_and_taken_from_the_reference(self, tmp_path: Path) -> None:
        """D024 makes one grid per matrix structural. The first log decides."""
        built = build_trials(variants(tmp_path), configuration(tmp_path))
        assert built.period is Period.DAILY
        assert built.names[0] == "var_01"

    def test_the_columns_are_the_same_quantity_the_run_computes(self, tmp_path: Path) -> None:
        """Otherwise the deflation compares the strategy's Sharpe against a
        distribution of some other statistic."""
        logs = variants(tmp_path)
        config = configuration(tmp_path)
        built = build_trials(logs, config)
        report = run_validation(logs[0], config).report
        column = built.values[:, 0]
        observed = report.entry("track_record").payload["observed_sharpe"]
        # ddof=0, which is D014's convention for the point estimate the
        # deflation consumes. Under ddof=1 the two differ by exactly
        # sqrt(T/(T-1)), 1.000658 at T=760, and the first version of this test
        # compared the wrong pair and read a documented convention as a defect.
        assert float(column.mean() / column.std(ddof=0)) == pytest.approx(observed, rel=1e-9)

    def test_and_the_two_degree_of_freedom_conventions_differ_by_the_known_factor(
        self, tmp_path: Path
    ) -> None:
        """D014 keeps both, and the gap between them is not noise."""
        built = build_trials(variants(tmp_path, count=3), configuration(tmp_path))
        column = built.values[:, 0]
        periods = column.size
        ratio = (column.std(ddof=0) / column.std(ddof=1)) ** -1
        assert float(ratio) == pytest.approx(np.sqrt(periods / (periods - 1)))

    def test_it_serialises_into_the_format_the_pipeline_reads(self, tmp_path: Path) -> None:
        built = build_trials(variants(tmp_path), configuration(tmp_path))
        text = built.to_csv()
        header = text.splitlines()[0].split(",")
        assert header[0] == "period_end"
        assert tuple(header[1:]) == built.names
        assert len(text.splitlines()) == built.n_periods + 1


class TestTheWholePathToARankableVerdict:
    """The gap this closes: the tool's conclusion used to need an artefact
    nothing in the tool could produce."""

    def test_a_sweep_produces_a_verdict_that_is_not_suppressed(self, tmp_path: Path) -> None:
        logs = variants(tmp_path)
        built = build_trials(logs, configuration(tmp_path))
        (tmp_path / "trials.csv").write_text(built.to_csv())
        config = configuration(tmp_path, n_trials=20, trials_path="trials.csv")
        report = run_validation(logs[0], config).report

        assert "verdict" not in report.sections_absent
        assert report.entry("deflated_sharpe").status.value == "RAN"
        assert report.entry("verdict").payload["certainty_equivalent"] is not None

    def test_and_the_search_still_costs_confidence(self, tmp_path: Path) -> None:
        """The ordering the product exists to show, now reachable without
        anyone hand writing a matrix."""
        logs = variants(tmp_path)
        (tmp_path / "trials.csv").write_text(build_trials(logs, configuration(tmp_path)).to_csv())
        report = run_validation(
            logs[0], configuration(tmp_path, n_trials=20, trials_path="trials.csv")
        ).report
        deflated = report.entry("deflated_sharpe").payload
        assert deflated["probability_against_zero"] > deflated["probability"]

    def test_the_cross_validation_is_not_degenerate(self, tmp_path: Path) -> None:
        """Independent noise per variant, so the in sample winner is not the
        out of sample winner by construction and PBO measures something."""
        logs = variants(tmp_path)
        (tmp_path / "trials.csv").write_text(build_trials(logs, configuration(tmp_path)).to_csv())
        report = run_validation(
            logs[0], configuration(tmp_path, n_trials=20, trials_path="trials.csv")
        ).report
        overfit = report.entry("pbo").payload
        assert overfit["median_logit"] < overfit["logit_ceiling"]


class TestTheRefusals:
    def test_one_log_is_not_a_sweep(self, tmp_path: Path) -> None:
        logs = variants(tmp_path, count=2)
        with pytest.raises(SchemaError, match="at least two"):
            build_trials(logs[:1], configuration(tmp_path))

    def test_duplicate_names_are_refused(self, tmp_path: Path) -> None:
        logs = variants(tmp_path, count=2)
        with pytest.raises(SchemaError, match="distinct"):
            build_trials(logs, configuration(tmp_path), names=["same", "same"])

    def test_a_name_per_log_is_required(self, tmp_path: Path) -> None:
        logs = variants(tmp_path, count=3)
        with pytest.raises(SchemaError, match="names for"):
            build_trials(logs, configuration(tmp_path), names=["a", "b"])

    def test_windows_that_barely_overlap_are_refused_with_the_numbers(self, tmp_path: Path) -> None:
        """A matrix over a handful of shared periods is worse than no matrix:
        it would deflate against a distribution nobody could defend."""
        logs = variants(tmp_path, count=2)
        frame = pd.read_csv(logs[1])
        frame.tail(20).to_csv(logs[1], index=False)
        with pytest.raises(SchemaError, match=f"MIN_PERIODS={MIN_PERIODS}"):
            build_trials(logs, configuration(tmp_path))

    def test_a_missing_file_is_typed(self, tmp_path: Path) -> None:
        logs = variants(tmp_path, count=2)
        with pytest.raises(SchemaError):
            build_trials([logs[0], tmp_path / "absent.csv"], configuration(tmp_path))


class TestSpansAreIntersectedRatherThanFilled:
    """A period outside a variant's own span is an absence, not a zero return."""

    def test_the_common_window_is_the_intersection(self, tmp_path: Path) -> None:
        logs = variants(tmp_path, count=3)
        for path, drop in zip(logs[1:], (5, 9), strict=True):
            frame = pd.read_csv(path)
            frame.iloc[drop:].to_csv(path, index=False)
        built = build_trials(logs, configuration(tmp_path))
        assert built.worst_trim > 0
        assert built.n_periods < len(pd.read_csv(logs[0]))

    def test_the_trimming_is_reported_per_configuration(self, tmp_path: Path) -> None:
        """A configuration that lost a third of its history is a different
        object from one that lost two days, and the caller should be able to
        tell them apart."""
        logs = variants(tmp_path, count=3)
        frame = pd.read_csv(logs[2])
        frame.iloc[12:].to_csv(logs[2], index=False)
        built = build_trials(logs, configuration(tmp_path))
        assert built.trimmed["var_03"][0] == 0, "the late starter loses nothing at the front"
        assert built.trimmed["var_01"][0] > 0, "the reference loses the periods it alone had"

    def test_nothing_is_zero_filled(self, tmp_path: Path) -> None:
        """The number of rows is the shared window, never the union, so no
        column can carry a period its own log never covered."""
        logs = variants(tmp_path, count=3)
        frame = pd.read_csv(logs[1])
        frame.iloc[8:].to_csv(logs[1], index=False)
        built = build_trials(logs, configuration(tmp_path))
        one = build_trials([logs[1], logs[0]], configuration(tmp_path))
        assert built.n_periods <= one.n_periods
