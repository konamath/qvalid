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
from urllib.parse import urlencode

import pytest

from qvalid.ui.form import MAPPED_FIELDS, RUN_FIELDS, build_files
from qvalid.ui.pages import finish_page, form_page, setup_page
from qvalid.ui.scratch import Scratch
from qvalid.ui.upload import Upload, parse_form

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


class TestTheTwoThingsTheFirstRealUserHit:
    """Both found by watching someone use it, not by review. See D067."""

    def test_a_file_that_is_not_a_trade_log_is_named_as_such(self, scratch: Scratch) -> None:
        """Uploading the trial matrix by mistake produced ten copies of "no
        column matched" beside a dead button, leaving the person hunting for
        ten mistakes when they had made one."""
        page = setup_page(upload(FIXTURES / "trials_winner.csv"), scratch)[1]
        assert "does not look like a trade log" in page
        assert "period_end" in page, "the person should see which columns it did find"

    def test_a_real_trade_log_is_not_accused_of_being_the_wrong_file(
        self, scratch: Scratch
    ) -> None:
        assert "does not look like a trade log" not in setup_page(upload(), scratch)[1]

    def test_the_menus_still_work_on_an_unrecognised_file(self, scratch: Scratch) -> None:
        """The banner is a diagnosis, not a refusal: a real log with unusual
        names is exactly what the menus are for."""
        page = setup_page(upload(FIXTURES / "trials_winner.csv"), scratch)[1]
        assert page.count("<select") >= len(MAPPED_FIELDS)

    @pytest.mark.parametrize("field", ["initial_capital", "seed", "risk_free_rate", "n_paths"])
    def test_a_required_number_coming_back_empty_is_refused(self, field: str) -> None:
        """It used to become the value the form had offered. A browser that
        rejects a decimal comma sends nothing, and the person who typed
        250000 got a report about 100000 with no sign that anything happened."""
        with pytest.raises(ValueError, match="came back empty"):
            build_files({**ANSWERS, field: ""})

    @pytest.mark.parametrize("field", ["ruin_barrier", "n_trials"])
    def test_an_optional_number_coming_back_empty_is_simply_omitted(self, field: str) -> None:
        """Blank means something here: skip the section, declare no search."""
        _, _, run = build_files({**ANSWERS, field: ""})
        assert f"{field}:" not in run

    def test_the_offered_value_never_reaches_the_configuration_on_its_own(self) -> None:
        """The value that fills the box and the value the assembler accepts
        were the same thing, which is what made the substitution invisible."""
        _, _, run = build_files({**ANSWERS, "initial_capital": "250000"})
        assert "initial_capital: 250000" in run
        assert "100000" not in run


class TestTheSeamNothingTested:
    """The browser path had never once worked. See D069.

    The configuration form declared no ``enctype``, so a browser posted
    ``application/x-www-form-urlencoded``, the server parsed only multipart,
    and every field arrived empty including the token. The person was told
    their upload had expired, which was false and unfixable by retrying.

    Every test passed because every test called the page functions with a
    dictionary already built. The one layer between the browser and the server
    was the one layer nothing exercised, so these tests go through it.
    """

    def test_a_form_posted_the_way_a_browser_posts_it_arrives_intact(self) -> None:
        body = urlencode(ANSWERS).encode()
        parsed = parse_form(body, "application/x-www-form-urlencoded")
        assert {name: item.value for name, item in parsed.items()} == ANSWERS

    def test_the_encoding_a_browser_uses_for_a_file_still_works(self) -> None:
        boundary = "----X"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="token"\r\n\r\nabc\r\n'
            f"--{boundary}--\r\n"
        ).encode()
        parsed = parse_form(body, f"multipart/form-data; boundary={boundary}")
        assert parsed["token"].value == "abc"

    def test_a_blank_field_survives_rather_than_vanishing(self) -> None:
        """``n_trials`` empty means "no search declared", which is a decision.
        A parser that dropped it would turn a declaration into an absence."""
        parsed = parse_form(b"n_trials=&seed=7", "application/x-www-form-urlencoded")
        assert parsed["n_trials"].value == ""
        assert parsed["seed"].value == "7"

    def test_the_whole_walk_through_the_parser(self, scratch: Scratch) -> None:
        """The test that would have caught it: setup, encode as a browser
        would, parse, finish."""
        page = setup_page(upload(), scratch)[1]
        sent = {"token": token_of(page), **ANSWERS, "sym__GER40__currency": "EUR"}
        parsed = parse_form(urlencode(sent).encode(), "application/x-www-form-urlencoded")
        status, out = finish_page(parsed, scratch)
        assert status == 200, out[:400]
        assert "Keep these three files" in out

    def test_every_form_that_carries_a_file_declares_multipart(self, scratch: Scratch) -> None:
        """The other half of the same seam: a file input under urlencoded
        sends the filename and not the bytes."""
        pages = [form_page(), setup_page(upload(), scratch)[1]]
        for page in pages:
            for form in re.findall(r"<form[^>]*>.*?</form>", page, re.S):
                opening = form[: form.index(">") + 1]
                if 'type="file"' in form:
                    assert "multipart/form-data" in opening, opening


class TestCurrencyIsAskedForRatherThanInvented:
    """D069's smaller half, and it blocked the run just as completely."""

    def test_a_blank_currency_is_refused_with_a_readable_reason(self) -> None:
        """It used to write ``UNSPECIFIED``, which the symbology schema then
        rejected at the last step with a pydantic error naming a value the
        person had never typed."""
        with pytest.raises(ValueError, match="three letter currency"):
            build_files({**ANSWERS, "sym__GER40__currency": ""})

    def test_and_so_is_something_that_is_not_a_code(self) -> None:
        with pytest.raises(ValueError, match="three letter currency"):
            build_files({**ANSWERS, "sym__GER40__currency": "euros"})

    def test_a_code_is_upper_cased_and_written_through(self) -> None:
        _, symbology, _ = build_files({**ANSWERS, "sym__GER40__currency": "eur"})
        assert "currency: EUR" in symbology

    def test_the_venue_stays_optional_because_nothing_validates_it(self) -> None:
        _, symbology, _ = build_files({**ANSWERS, "sym__GER40__venue": ""})
        assert "venue: UNSPECIFIED" in symbology

    def test_the_form_offers_codes_instead_of_a_free_text_box(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        assert 'name="sym__GER40__currency"' in page
        assert "<select" in page[page.index('name="sym__GER40__currency"') - 40 :][:60]


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


class TestTheLandingPageLeadsWithWhatANewArrivalCanDo:
    """D068. The order was wrong and two people had to look at it to see."""

    def test_the_guided_route_comes_first(self) -> None:
        """It used to open by asking for a run configuration, which is one of
        three YAML files nobody has the first time, with the guided route below
        a rule under the heading "No configuration yet?". The first thing shown
        was the one thing a new arrival could not do."""
        page = form_page()
        assert page.index('action="/setup"') < page.index('action="/run"')

    def test_the_first_thing_asked_for_is_the_only_file_they_certainly_have(self) -> None:
        page = form_page()
        head = page[: page.index('action="/run"')]
        assert 'type="file"' in head
        assert 'name="config"' not in head

    def test_the_expert_route_survives_and_says_who_it_is_for(self) -> None:
        """Repeating a run exactly is what the configuration path is for, and
        it is the reproducibility claim of D016 made usable."""
        page = form_page()
        assert "Already have the three files" in page
        assert 'action="/run"' in page

    def test_a_refusal_still_keeps_the_typed_path(self) -> None:
        page = form_page({"config": "/tmp/run.yaml"}, error="no such file")
        assert "/tmp/run.yaml" in page
        assert "no such file" in page
