"""The whole path on an export written in none of this project's vocabularies. See D062.

Every earlier test reads a fixture whose columns this project named, whose
timestamps this project formatted and whose conventions this project chose.
That checks the code against itself. ``foreign_mt5.csv`` is a MetaTrader style
export with day first stamps, negative costs and a P&L column before costs, and
walking it end to end found four defects that seven hundred passing tests did
not.

The important one: ``inspect`` printed ``SIGNED`` as the alternative fee
convention. No such value exists, the enum is ``MAGNITUDE`` or ``NEGATED``, so
a person following the hint got a validation error. It is caught here
structurally rather than by eye, in :class:`TestDraftNamesOnlyRealValues`.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from qvalid.adapters.probe import probe_trade_log, read_declarations
from qvalid.adapters.suggest import suggest_columns
from qvalid.adapters.tradelog import FeeConvention, PnlConvention, PnlSource, load_mapping
from qvalid.cli import app
from qvalid.pipeline import run_validation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = FIXTURES / "foreign_mt5.csv"
MAPPING = FIXTURES / "foreign_mapping.yaml"

TRUE_MULTIPLIER = 25.0
"""Known because the export was constructed, and known to nothing in the code."""


def draft_of(log: Path) -> str:
    result = CliRunner().invoke(app, ["inspect", str(log)])
    assert result.exit_code == 0
    return result.stdout


class TestDraftNamesOnlyRealValues:
    """The defect the first foreign file found, made structural."""

    @pytest.mark.parametrize(
        ("field", "enum"),
        [
            ("fee_convention", FeeConvention),
            ("pnl_convention", PnlConvention),
            ("pnl_source", PnlSource),
        ],
    )
    def test_every_value_the_draft_mentions_exists(self, field: str, enum: type) -> None:
        """``inspect`` suggested ``SIGNED``, which is not a FeeConvention. The
        person who followed the comment got a pydantic error instead of a run,
        and no test failed, because nothing compared the prose to the enum."""
        valid = {member.value for member in enum}
        line = next(item for item in draft_of(LOG).splitlines() if item.startswith(f"{field}:"))
        mentioned = {word.strip(".,`'\"") for word in line.replace(":", " ").split()}
        assert mentioned & valid, f"{field} names no valid value at all"
        prose = {"DECIDE", "NOT", "AND", "OR", "THE"}
        for word in mentioned:
            # isalpha keeps decision identifiers like D017 out; without it the
            # filter reads a cross reference as a proposed enum value.
            if word.isalpha() and word.isupper() and len(word) > 2 and word not in prose:
                assert word in valid, f"{field} mentions {word!r}, which is not a {enum.__name__}"

    def test_the_draft_marks_the_four_it_cannot_read(self) -> None:
        """A header shows none of them, and all four were wrong for this file."""
        draft = draft_of(LOG)
        for field in ("fee_convention", "pnl_convention", "timestamp_format", "timezone"):
            line = next(item for item in draft.splitlines() if item.startswith(f"{field}:"))
            assert "DECIDE" in line, f"{field} is printed as though it had been read"


class TestTheDraftIsWrongInPreciselyTheKnownWays:
    """Not a complaint about the draft: a record of what a header cannot carry."""

    def test_the_columns_are_right(self) -> None:
        with LOG.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        found = suggest_columns(header)
        assert found.is_complete
        assert found.columns == yaml.safe_load(MAPPING.read_text())["columns"]

    @pytest.mark.parametrize(
        ("field", "guessed"),
        [
            ("fee_convention", "MAGNITUDE"),
            ("pnl_convention", "NET"),
            ("timestamp_format", "%Y-%m-%d %H:%M:%S"),
        ],
    )
    def test_and_the_conventions_are_wrong(self, field: str, guessed: str) -> None:
        truth = yaml.safe_load(MAPPING.read_text())[field]
        assert truth != guessed


class TestProbeCatchesWhatTheDraftGotWrong:
    """Two of the three wrong guesses are visible one line into the data."""

    def test_it_reads_the_cost_column_as_negative(self) -> None:
        seen = read_declarations(LOG, load_mapping(MAPPING))
        assert seen.fee_sign == "NEGATIVE"
        assert seen.fee_convention_implied == "NEGATED"

    def test_it_shows_the_stamp_verbatim_so_the_format_can_be_checked(self) -> None:
        seen = read_declarations(LOG, load_mapping(MAPPING))
        assert seen.sample_entry_ts == "08.03.2022 14:46:00"
        assert seen.timestamp_format_parses is True

    def test_it_reports_a_format_that_cannot_parse_its_own_input(self, tmp_path: Path) -> None:
        text = MAPPING.read_text().replace("%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
        wrong = tmp_path / "m.yaml"
        wrong.write_text(text)
        assert read_declarations(LOG, load_mapping(wrong)).timestamp_format_parses is False

    def test_it_recovers_the_multiplier_nobody_told_it(self) -> None:
        entry = probe_trade_log(LOG, load_mapping(MAPPING))[0]
        assert entry.symbol == "GER40"
        assert entry.implied == pytest.approx(TRUE_MULTIPLIER)

    def test_and_contradicts_a_mapping_that_declares_the_wrong_convention(
        self, tmp_path: Path
    ) -> None:
        """The file is gross of costs. Told it is net, the probe still says
        gross, which is the disagreement the command exists to produce."""
        wrong = tmp_path / "m.yaml"
        wrong.write_text(
            MAPPING.read_text().replace("pnl_convention: GROSS", "pnl_convention: NET")
        )
        assert probe_trade_log(LOG, load_mapping(wrong))[0].convention == "GROSS"


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> object:
    """One run, shared: the bootstrap is the slow part and nothing mutates it."""
    config = tmp_path_factory.mktemp("foreign") / "run.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "symbology_path": str(FIXTURES / "foreign_symbology.yaml"),
                "mapping_path": str(MAPPING),
                "initial_capital": 100000.0,
                "basis": "FIXED_INITIAL",
                "seed": 20260805,
                "risk_free_rate": 0.032,
                "n_paths": 200,
            }
        )
    )
    return run_validation(LOG, config).report


class TestTheCorrectedConfigurationRunsAndIsRight:
    """An end to end run whose numbers are checked outside the library."""

    def test_it_runs_at_all(self, report: object) -> None:
        assert report.sections_run  # type: ignore[attr-defined]

    def test_the_trade_count_and_expectancy_match_a_hand_calculation(self, report: object) -> None:
        """Computed here from the raw CSV, without importing anything that
        computed the report, so agreement is evidence and not a tautology."""
        frame = pd.read_csv(LOG)
        side = np.where(frame["Type"] == "buy", 1.0, -1.0)
        gross = side * (frame["Close Price"] - frame["Open Price"]) * frame["Volume"]
        net = gross * TRUE_MULTIPLIER - (-frame["Commission"])
        payload = report.entry("trade_metrics").payload  # type: ignore[attr-defined]
        assert payload["n_trades"] == len(frame)
        assert payload["expectancy"] == pytest.approx(float((net / 100000.0).mean()))
        assert payload["hit_rate"] == pytest.approx(float((net > 0).mean()))

    def test_the_coherence_identity_accepted_the_declared_multiplier(self, report: object) -> None:
        """Import would have raised had the multiplier disagreed with the file,
        so reaching here is the check. Stated because it is the one invariant
        that ties the probe's recovered 25 to the declared 25."""
        assert report.entry("trade_metrics").status.value == "RAN"  # type: ignore[attr-defined]

    def test_absent_sections_each_carry_their_own_reason(self, report: object) -> None:
        absent = report.sections_absent  # type: ignore[attr-defined]
        assert absent, "a log with no trials and no reference series must leave sections absent"
        for name in absent:
            assert report.entry(name).reason  # type: ignore[attr-defined]
