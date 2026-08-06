"""Configuring a run in the browser, with controls rather than YAML. See D063 and D066.

The first version of this flow showed three boxes of raw YAML. That removed the
need to find a template and left everything else in place: the person still had
to read comments to learn which words were legal, still had to know
``strftime``, and still had to type column names they could not see.

What matters most here is which defaults the form arrives with. Every one is
either read from the person's own file, in which case a test says so and says
where it was read from, or left empty because the file cannot settle it.
"""

from __future__ import annotations

import ast
import re
from html import unescape
from pathlib import Path

import pytest

from qvalid.ui.form import RUN_FIELDS, build_files
from qvalid.ui.pages import finish_page, form_page, setup_page
from qvalid.ui.scratch import Scratch
from qvalid.ui.upload import Upload

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FOREIGN = FIXTURES / "foreign_mt5.csv"
UI = Path(__file__).resolve().parents[2] / "src/qvalid/ui"

ANSWERS = {
    "col__trade_id": "Ticket",
    "col__symbol": "Symbol",
    "col__side": "Type",
    "col__qty": "Volume",
    "col__entry_ts": "Open Time",
    "col__exit_ts": "Close Time",
    "col__entry_px": "Open Price",
    "col__exit_px": "Close Price",
    "col__fees": "Commission",
    "col__pnl": "Profit",
    "tag__Swap": "on",
    "fee_convention": "NEGATED",
    "pnl_convention": "GROSS",
    "timestamp_format": "%d.%m.%Y %H:%M:%S",
    "timezone": "Europe/Berlin",
    "sym__GER40__multiplier": "25.0",
    "sym__GER40__tick_size": "0.5",
    "sym__GER40__venue": "EUREX",
    "sym__GER40__currency": "EUR",
    "initial_capital": "100000",
    "seed": "20260805",
    "risk_free_rate": "0.02",
    "n_paths": "200",
    "ruin_barrier": "85000",
    "n_trials": "",
}
"""A completed form for ``foreign_mt5.csv``, corrections and all."""


@pytest.fixture
def scratch() -> Scratch:
    store = Scratch()
    yield store
    store.close()


def upload(path: Path = FOREIGN) -> dict[str, Upload]:
    return {"log": Upload(filename=path.name, content=path.read_bytes())}


def token_of(page: str) -> str:
    match = re.search(r'name="token" value="([^"]+)"', page)
    assert match, "no token, so the second request cannot find the upload"
    return match.group(1)


def selected(page: str, field: str) -> str | None:
    """What a named select arrives pre selected with."""
    block = re.search(rf'name="{re.escape(field)}"[^>]*>(.*?)</select>', page, re.S)
    assert block, f"no select named {field}"
    chosen = re.search(r'value="([^"]*)" selected', block.group(1))
    return unescape(chosen.group(1)) if chosen else None


def submit(fields: dict[str, str], scratch: Scratch, page: str) -> tuple[int, str]:
    sent = {"token": token_of(page), **fields}
    return finish_page({key: Upload(value=value) for key, value in sent.items()}, scratch)


class TestTheFormArrivesFilledInFromTheFile:
    def test_it_shows_the_persons_own_rows(self, scratch: Scratch) -> None:
        """Column names are abstractions until you can see what is under them."""
        page = setup_page(upload(), scratch)[1]
        assert "41200000" in page
        assert "GER40" in page

    def test_every_column_is_pre_selected_from_the_header(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        for field in ("trade_id", "symbol", "side", "qty", "entry_ts", "exit_px", "pnl"):
            assert selected(page, f"col__{field}") == ANSWERS[f"col__{field}"]

    def test_the_fee_convention_comes_from_the_sign_of_the_column(self, scratch: Scratch) -> None:
        """This export's costs are negative, and the draft used to say
        MAGNITUDE because a header cannot show a sign."""
        page = setup_page(upload(), scratch)[1]
        assert selected(page, "fee_convention") == "NEGATED"
        assert "your cost column is negative" in page

    def test_the_timestamp_format_is_read_from_the_column(self, scratch: Scratch) -> None:
        """The person should not have to know strftime for a fact that is in
        their file. See D066."""
        page = setup_page(upload(), scratch)[1]
        assert selected(page, "timestamp_format") == "%d.%m.%Y %H:%M:%S"
        assert "reads your column" in page

    def test_the_implied_multiplier_is_shown_beside_an_empty_box(self, scratch: Scratch) -> None:
        """D007 unchanged: shown, never filled in."""
        page = setup_page(upload(), scratch)[1]
        assert "implies 25" in page
        box = re.search(r'name="sym__GER40__multiplier"[^>]*>', page)
        assert box and 'value=""' in box.group(0)

    def test_the_trial_count_is_left_empty(self, scratch: Scratch) -> None:
        """A default here would fabricate the input that decides the verdict."""
        page = setup_page(upload(), scratch)[1]
        box = re.search(r'name="n_trials"[^>]*>', page)
        assert box and 'value=""' in box.group(0)

    def test_the_time_zone_is_asked_and_never_guessed(self, scratch: Scratch) -> None:
        """Reading the machine's own zone would make one file produce two
        different reports on two laptops."""
        page = setup_page(upload(), scratch)[1]
        assert selected(page, "timezone") == "UTC"
        assert "same file must not" in page


class TestTheWalkFromUploadToReport:
    def test_a_completed_form_produces_the_report(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        status, out = submit(ANSWERS, scratch, page)
        assert status == 200
        assert "SUPPRESSED" in out, "no trials declared, so D004 must suppress the verdict"

    def test_the_three_files_come_back_so_the_run_can_be_reproduced(self, scratch: Scratch) -> None:
        """D016: the file is the provenance, and a temporary folder disappears."""
        page = setup_page(upload(), scratch)[1]
        out = submit(ANSWERS, scratch, page)[1]
        for name in ("mapping.yaml", "symbology.yaml", "run.yaml"):
            assert name in out
        assert "Keep these three files" in out

    def test_two_fields_claiming_one_column_is_refused(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        status, out = submit({**ANSWERS, "col__exit_px": "Open Price"}, scratch, page)
        assert status == 400
        assert "both claim" in out

    def test_and_the_refusal_keeps_everything_already_answered(self, scratch: Scratch) -> None:
        """Losing a filled form to one wrong menu is how people give up."""
        page = setup_page(upload(), scratch)[1]
        out = submit({**ANSWERS, "col__exit_px": "Open Price"}, scratch, page)[1]
        assert 'value="25.0"' in out
        assert selected(out, "fee_convention") == "NEGATED"
        assert selected(out, "timezone") == "Europe/Berlin"

    def test_a_configuration_the_engine_refuses_comes_back_as_the_form(
        self, scratch: Scratch
    ) -> None:
        """Not a traceback, and not a partial report: ``02`` section 7 is clear
        that half a rendered page is the worst version of absence."""
        page = setup_page(upload(), scratch)[1]
        status, out = submit({**ANSWERS, "sym__GER40__multiplier": "1.0"}, scratch, page)
        assert status == 400
        assert "<select" in out

    def test_an_expired_token_is_a_refusal(self, scratch: Scratch) -> None:
        status, out = finish_page({"token": Upload(value="nope")}, scratch)
        assert status == 400
        assert "expired" in out


class TestBuildFilesIsTheOnlyAuthority:
    """One assembler, in Python. A script that also built YAML would drift."""

    def test_it_writes_the_columns_it_was_given(self) -> None:
        mapping, _, _ = build_files(ANSWERS)
        assert "entry_ts: Open Time" in mapping
        assert "fee_convention: NEGATED" in mapping
        assert 'timestamp_format: "%d.%m.%Y %H:%M:%S"' in mapping

    def test_it_refuses_a_missing_column_rather_than_defaulting(self) -> None:
        with pytest.raises(ValueError, match="no column chosen"):
            build_files({**ANSWERS, "col__pnl": ""})

    def test_it_refuses_a_collision_rather_than_picking(self) -> None:
        with pytest.raises(ValueError, match="both claim"):
            build_files({**ANSWERS, "col__exit_px": "Open Price"})

    def test_it_refuses_a_symbol_without_a_multiplier(self) -> None:
        with pytest.raises(ValueError, match="multiplier"):
            build_files({**ANSWERS, "sym__GER40__multiplier": ""})

    def test_an_omitted_trial_count_stays_omitted(self) -> None:
        """Present with a value would be a fabricated declaration; D004."""
        _, _, run = build_files(ANSWERS)
        assert "n_trials" not in run

    def test_a_given_trial_count_is_carried_through(self) -> None:
        _, _, run = build_files({**ANSWERS, "n_trials": "20"})
        assert "n_trials: 20" in run

    def test_the_free_text_box_wins_when_something_else_was_picked(self) -> None:
        mapping, _, _ = build_files(
            {**ANSWERS, "timezone": "__custom__", "timezone_custom": "Pacific/Auckland"}
        )
        assert "timezone: Pacific/Auckland" in mapping

    def test_the_run_configuration_names_the_two_files_beside_it(self) -> None:
        _, _, run = build_files(ANSWERS)
        assert "mapping_path: mapping.yaml" in run
        assert "symbology_path: symbology.yaml" in run

    def test_every_run_field_reaches_the_configuration(self) -> None:
        """A field on the form that no file receives is a control that lies."""
        _, _, run = build_files({**ANSWERS, "n_trials": "20"})
        for name, _, _, _, _ in RUN_FIELDS:
            assert f"{name}:" in run


class TestScratch:
    def test_a_stored_log_keeps_its_own_name(self, scratch: Scratch) -> None:
        """D042 puts the name in the provenance."""
        stored = scratch.log_of(scratch.store("janeiro.csv", b"a,b\n1,2\n"))
        assert stored is not None and stored.name == "janeiro.csv"

    def test_a_filename_carrying_a_path_is_reduced_to_its_leaf(self, scratch: Scratch) -> None:
        stored = scratch.log_of(scratch.store("../../etc/trades.csv", b"a\n1\n"))
        assert stored is not None and stored.name == "trades.csv"

    def test_a_token_describing_a_route_reaches_nothing(self, scratch: Scratch) -> None:
        scratch.store("real.csv", b"a\n1\n")
        for hostile in ("..", "../..", "/etc", "", "."):
            assert scratch.folder_of(hostile) is None

    def test_the_oldest_upload_is_evicted_rather_than_filling_the_disk(self) -> None:
        store = Scratch(limit=2)
        try:
            first = store.store("a.csv", b"a\n1\n")
            store.store("b.csv", b"a\n1\n")
            store.store("c.csv", b"a\n1\n")
            assert store.folder_of(first) is None
        finally:
            store.close()

    def test_closing_removes_everything_and_is_safe_twice(self) -> None:
        store = Scratch()
        folder = store.folder_of(store.store("a.csv", b"a\n1\n"))
        assert folder is not None
        store.close()
        store.close()
        assert not folder.exists()


class TestStillNoCalculationInTheInterface:
    """``05``'s permanent rule, against the new modules too."""

    def test_the_interface_imports_no_core_module(self) -> None:
        offenders = [
            f"{path.name}:{node.lineno}"
            for path in sorted(UI.glob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("qvalid.core")
        ]
        assert offenders == []

    def test_the_script_assembles_no_configuration(self) -> None:
        """The inline script may highlight, toggle and gate. If it started
        writing YAML there would be two implementations of the configuration,
        and D063 exists because a second copy of this kind of thing had already
        gone wrong once."""
        script = (UI / "form.py").read_text(encoding="utf-8")
        opening = script.index("_SCRIPT = ")
        body = script[opening : script.index('"""', opening + 20)]
        for forbidden in ("yaml", "columns:", "mapping_path", "fee_convention:"):
            assert forbidden not in body

    def test_the_form_never_reaches_inside_the_report(self) -> None:
        source = (UI / "pages.py").read_text(encoding="utf-8")
        for attribute in (".payload", ".panel", ".provenance", ".sections_run"):
            assert attribute not in source


def test_the_landing_page_still_offers_the_guided_route() -> None:
    page = form_page()
    assert 'action="/setup"' in page
    assert "No configuration yet" in page
