"""Column suggestion: what it proposes, and what it refuses to propose. See D060.

The refusals carry most of the weight here. A guesser that always answers is
worse than no guesser, because a mapping that parses and means something other
than what the person has produces a report with no visible defect. So roughly
half of these tests assert that a field was left unresolved.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from qvalid.adapters.suggest import ALIASES, Suggestion, suggest_columns
from qvalid.adapters.tradelog import REQUIRED_FIELDS
from qvalid.cli import app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

METATRADER = [
    "Ticket",
    "Symbol",
    "Type",
    "Volume",
    "Open Time",
    "Close Time",
    "Open Price",
    "Close Price",
    "Commission",
    "Swap",
    "Profit",
]
"""A MetaTrader style header. Hostile in the way real exports are: no field is
spelled the way this project spells it, and ``Swap`` is a cost the mapping has
no slot for."""


def header_of(name: str) -> list[str]:
    """Read only the first row of a fixture, which is all the suggester sees."""
    with (FIXTURES / name).open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle))


class TestAgreesWithTheMappingAPersonWrote:
    """The fixture's committed mapping was written by hand before this existed."""

    def test_generic_fixture_reproduces_the_committed_mapping_exactly(self) -> None:
        written = yaml.safe_load((FIXTURES / "mapping_generic.yaml").read_text())
        found = suggest_columns(header_of("trades_generic.csv"))
        assert found.columns == written["columns"]

    def test_and_offers_the_same_tag_column(self) -> None:
        written = yaml.safe_load((FIXTURES / "mapping_generic.yaml").read_text())
        assert (
            list(suggest_columns(header_of("trades_generic.csv")).unused) == written["tag_columns"]
        )

    @pytest.mark.parametrize("name", ["trades_generic.csv", "trades_long.csv"])
    def test_both_single_row_fixtures_resolve_completely(self, name: str) -> None:
        assert suggest_columns(header_of(name)).is_complete


class TestUnfamiliarVocabulary:
    """The point of the feature: a header nobody wrote this project for."""

    def test_metatrader_header_resolves_every_field(self) -> None:
        found = suggest_columns(METATRADER)
        assert found.is_complete, f"missing {found.missing}, ambiguous {found.ambiguous}"

    def test_and_puts_each_field_on_the_column_that_means_it(self) -> None:
        assert suggest_columns(METATRADER).columns == {
            "trade_id": "Ticket",
            "symbol": "Symbol",
            "side": "Type",
            "qty": "Volume",
            "entry_ts": "Open Time",
            "exit_ts": "Close Time",
            "entry_px": "Open Price",
            "exit_px": "Close Price",
            "fees": "Commission",
            "pnl": "Profit",
        }

    def test_leaves_swap_unclaimed_rather_than_folding_it_into_fees(self) -> None:
        """``Swap`` is an overnight financing cost, and whether it belongs in
        ``fees`` changes every net number. That is the person's call: silently
        adding it, or silently dropping it, both produce a plausible report."""
        assert suggest_columns(METATRADER).unused == ("Swap",)

    def test_separators_do_not_matter(self) -> None:
        spellings = ["Open Time", "open_time", "OpenTime", "open-time", "OPEN TIME"]
        found = {
            suggest_columns([*METATRADER[:4], s, *METATRADER[5:]]).columns["entry_ts"]
            for s in spellings
        }
        assert found == set(spellings)


class TestRefusesRatherThanGuesses:
    """Every test here asserts an absence. That is the design."""

    def test_a_field_with_no_plausible_column_is_reported_missing(self) -> None:
        found = suggest_columns([c for c in METATRADER if c != "Ticket"])
        assert "trade_id" in found.missing
        assert "trade_id" not in found.columns
        assert not found.is_complete

    def test_two_fields_wanting_one_column_leaves_both_unresolved(self) -> None:
        """``Price`` alone cannot be both legs, and choosing one puts a real
        number in the wrong side of the P&L identity."""
        found = suggest_columns(
            [
                "Ticket",
                "Symbol",
                "Type",
                "Volume",
                "Open Time",
                "Close Time",
                "Price",
                "Commission",
                "Profit",
            ]
        )
        assert "Price" not in found.columns.values()
        assert not found.is_complete

    def test_a_collision_leaves_every_claimant_unresolved_not_all_but_the_first(self) -> None:
        """The order of ``REQUIRED_FIELDS`` is not evidence about a CSV."""
        found = suggest_columns(
            ["Ticket", "Symbol", "Type", "Volume", "Open Time", "Close Time", "Price", "Profit"]
        )
        assert set(found.ambiguous) >= {"entry_px", "exit_px"}
        assert "Price" not in found.columns.values()

    def test_an_exact_name_beats_a_field_that_only_reached_it_by_prefix(self) -> None:
        """``Entry`` is exactly what ``entry_ts`` is called; ``entry_px`` only
        gets there because ``entry_price`` happens to start with it. Evidence
        over inference is the one asymmetry allowed."""
        found = suggest_columns(
            ["Ticket", "Symbol", "Side", "Qty", "Entry", "Close Time", "Close Price", "Fee", "P/L"]
        )
        assert found.columns["entry_ts"] == "Entry"
        assert found.ambiguous["entry_px"] == ("Entry",)

    def test_a_contested_column_is_not_offered_as_a_free_label(self) -> None:
        """Listing it under ``tag_columns`` would invite someone to settle the
        collision by deleting the evidence that there was one."""
        found = suggest_columns(
            ["Ticket", "Symbol", "Type", "Volume", "Open Time", "Close Time", "Price", "Profit"]
        )
        assert "Price" not in found.unused

    def test_never_matches_by_edit_distance(self) -> None:
        """``exit_price`` and ``entry_price`` differ by three characters. A
        fuzzy matcher would pair them, the P&L identity would then fail, and
        the coherence check would blame the contract multiplier."""
        header = [c for c in METATRADER if c != "Close Price"] + ["Entry Price"]
        found = suggest_columns(header)
        assert "exit_px" not in found.columns
        assert "exit_px" in found.missing

    def test_a_column_is_never_claimed_by_two_fields(self) -> None:
        for header in (METATRADER, header_of("trades_generic.csv"), ["Price", "Time", "Qty"]):
            taken = list(suggest_columns(header).columns.values())
            assert len(taken) == len(set(taken))

    def test_a_mute_short_column_does_not_beat_a_real_one(self) -> None:
        """Every alias starts with some single letter, so an ungated truncation
        match reads a column called ``e`` as the entry timestamp. Measured over
        three headers, counting fields given the wrong column or none: ungated
        1 wrong, gated at four characters 2 wrong (it rejects ``Sym`` and
        ``Ref``, which people write), gated at three 0."""
        header = ["Ref", "e", "x", "Symbol", "Side", "Qty", "EntryStamp", "ExitStamp", "Fee", "P/L"]
        found = suggest_columns(header)
        assert found.columns["entry_ts"] == "EntryStamp"
        assert "e" in found.unused and "x" in found.unused

    def test_but_a_three_letter_abbreviation_still_matches(self) -> None:
        found = suggest_columns(
            ["Ref", "Sym", "Side", "Qty", "Entry Time", "Exit Time", "Open", "Close", "Fee", "P/L"]
        )
        assert found.columns["symbol"] == "Sym"
        assert found.columns["trade_id"] == "Ref"


class TestReadsTheHeaderAndNothingElse:
    """Inferring from values would hide the inference that produced the Sharpe."""

    def test_the_module_opens_no_file(self) -> None:
        source = (
            Path(__file__).resolve().parents[2] / "src/qvalid/adapters/suggest.py"
        ).read_text()
        for forbidden in ("open(", "read_csv", "import csv", "Path("):
            assert forbidden not in source

    def test_an_empty_header_reports_every_field_missing_and_does_not_raise(self) -> None:
        found = suggest_columns([])
        assert set(found.missing) == {*REQUIRED_FIELDS, "pnl"}
        assert found.columns == {} and found.unused == ()

    def test_aliases_cover_every_field_the_mapping_requires(self) -> None:
        assert set(ALIASES) == {*REQUIRED_FIELDS, "pnl"}

    def test_a_suggestion_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            suggest_columns(METATRADER).unused = ()  # type: ignore[misc]

    def test_an_empty_suggestion_is_not_complete(self) -> None:
        assert not Suggestion(missing=("qty",)).is_complete
        assert not Suggestion(ambiguous={"qty": ("a", "b")}).is_complete


class TestInspectCommand:
    """What the person actually sees."""

    def test_prints_yaml_that_parses(self) -> None:
        result = CliRunner().invoke(app, ["inspect", str(FIXTURES / "trades_generic.csv")])
        assert result.exit_code == 0
        parsed = yaml.safe_load(result.stdout)
        assert (
            parsed["columns"]
            == yaml.safe_load((FIXTURES / "mapping_generic.yaml").read_text())["columns"]
        )

    def test_writes_nothing(self, tmp_path: Path) -> None:
        """D016 makes the mapping provenance, so the file has to be the
        person's. This prints; it does not save."""
        log = tmp_path / "trades.csv"
        log.write_text((FIXTURES / "trades_generic.csv").read_text())
        before = set(tmp_path.iterdir())
        CliRunner().invoke(app, ["inspect", str(log)])
        assert set(tmp_path.iterdir()) == before

    def test_marks_what_it_could_not_resolve_instead_of_omitting_it(self, tmp_path: Path) -> None:
        log = tmp_path / "partial.csv"
        log.write_text(
            "Symbol,Type,Volume,Open Time,Close Time,Open Price,Close Price,Commission,Profit\n"
        )
        result = CliRunner().invoke(app, ["inspect", str(log)])
        assert "trade_id:" in result.stdout
        assert "NOT FOUND" in result.stdout

    def test_a_missing_file_is_an_error_not_an_empty_draft(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, ["inspect", str(tmp_path / "absent.csv")])
        assert result.exit_code == 2

    def test_an_empty_file_is_an_error(self, tmp_path: Path) -> None:
        log = tmp_path / "empty.csv"
        log.write_text("")
        assert CliRunner().invoke(app, ["inspect", str(log)]).exit_code == 2
