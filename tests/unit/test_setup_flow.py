"""Guided setup in the browser: upload a log, get three drafts, run. See D063.

The interface removed the friction of typing a path and left the real one in
place: a person arriving with only a CSV still had to write three YAML files by
hand before anything happened. These tests cover that path, and one of them
matters more than the rest.

:class:`TestTheTwoFrontEndsCannotDrift` asserts the browser and the command line
emit the **same bytes**. D062 was a comment in a draft naming an enum value that
did not exist, found only by walking a real file. Two copies of that prose would
double the surface, so there is one copy and this is what holds it to one.
"""

from __future__ import annotations

import ast
import csv
import html
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qvalid.cli import app
from qvalid.drafts import mapping_draft, run_config_draft, symbology_draft
from qvalid.ui.pages import SETUP_FILES, finish_page, form_page, setup_page
from qvalid.ui.scratch import Scratch
from qvalid.ui.upload import Upload

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = FIXTURES / "foreign_mt5.csv"
UI = Path(__file__).resolve().parents[2] / "src/qvalid/ui"


@pytest.fixture
def scratch() -> Scratch:
    store = Scratch()
    yield store
    store.close()


def upload(path: Path = LOG) -> dict[str, Upload]:
    return {"log": Upload(filename=path.name, content=path.read_bytes())}


def boxes(page: str) -> dict[str, str]:
    """The three textareas, unescaped, as the browser would send them back."""
    found = re.findall(r'<textarea name="(\w+)"[^>]*>(.*?)</textarea>', page, re.S)
    return {name: html.unescape(body) for name, body in found}


def token_of(page: str) -> str:
    match = re.search(r'name="token" value="([^"]+)"', page)
    assert match, "the page carries no token, so the second request cannot find the log"
    return match.group(1)


class TestTheTwoFrontEndsCannotDrift:
    """One copy of the drafts, asserted rather than intended."""

    def test_the_browser_shows_exactly_what_inspect_prints(self, scratch: Scratch) -> None:
        printed = CliRunner().invoke(app, ["inspect", str(LOG)]).stdout.strip()
        shown = boxes(setup_page(upload(), scratch)[1])["mapping"].strip()
        assert shown == printed

    def test_and_exactly_what_probe_prints_for_the_symbology(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        mapping_path = FIXTURES / "foreign_mapping.yaml"
        printed = CliRunner().invoke(app, ["probe", str(LOG), "-m", str(mapping_path)]).stdout
        # The command was given the corrected mapping and the browser drafts its
        # own, so only the part that does not depend on the conventions can be
        # compared: the symbols, and the multiplier the arithmetic recovers.
        assert "implied 25" in printed
        assert "implied 25" in boxes(page)["symbology"]

    def test_the_draft_helpers_are_the_only_place_this_text_exists(self) -> None:
        """A phrase from a draft appearing in two modules is drift starting."""
        marker = "The multipliers are NOT filled in"
        owners = [
            path.name
            for path in (Path(__file__).resolve().parents[2] / "src/qvalid").rglob("*.py")
            if marker in path.read_text(encoding="utf-8")
        ]
        assert owners == ["drafts.py"]


class TestTheDraftedPage:
    def test_it_offers_all_three_files_at_once(self, scratch: Scratch) -> None:
        status, page = setup_page(upload(), scratch)
        assert status == 200
        assert set(boxes(page)) == {key for key, _, _ in SETUP_FILES}

    def test_it_shows_the_evidence_beside_the_drafts(self, scratch: Scratch) -> None:
        """The point of drafting in a browser rather than a template: the
        disagreements are visible at the moment of deciding."""
        page = setup_page(upload(), scratch)[1]
        assert "implied 25" in page
        assert "NEGATIVE in the file" in page
        assert "DOES NOT PARSE" in page

    def test_it_marks_the_four_the_header_cannot_show(self, scratch: Scratch) -> None:
        mapping = boxes(setup_page(upload(), scratch)[1])["mapping"]
        for field in ("fee_convention", "pnl_convention", "timestamp_format", "timezone"):
            line = next(item for item in mapping.splitlines() if item.startswith(f"{field}:"))
            assert "DECIDE" in line

    def test_a_header_it_cannot_resolve_leaves_the_symbology_undrafted(
        self, scratch: Scratch, tmp_path: Path
    ) -> None:
        """An empty ``symbols:`` would read as a finding of no symbols."""
        odd = tmp_path / "odd.csv"
        odd.write_text("a,b,c\n1,2,3\n")
        page = setup_page(upload(odd), scratch)[1]
        assert "Not drafted" in boxes(page)["symbology"]

    def test_no_file_is_refused_with_the_form(self, scratch: Scratch) -> None:
        status, page = setup_page({}, scratch)
        assert status == 400
        assert "choose a trade log" in page

    def test_an_empty_upload_is_refused_by_name(self, scratch: Scratch) -> None:
        status, page = setup_page({"log": Upload(filename="void.csv", content=b"")}, scratch)
        assert status == 400
        assert "void.csv" in page

    def test_a_file_with_only_a_header_still_drafts_the_mapping(
        self, scratch: Scratch, tmp_path: Path
    ) -> None:
        empty = tmp_path / "headers.csv"
        empty.write_text(LOG.read_text().splitlines()[0] + "\n")
        status, page = setup_page(upload(empty), scratch)
        assert status == 200
        assert "trade_id: Ticket" in boxes(page)["mapping"]


class TestTheWalkFromUploadToReport:
    def test_a_corrected_configuration_produces_the_report(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        drafted = boxes(page)
        mapping = (
            drafted["mapping"]
            .replace("fee_convention: MAGNITUDE", "fee_convention: NEGATED")
            .replace("pnl_convention: NET", "pnl_convention: GROSS")
            .replace('"%Y-%m-%d %H:%M:%S"', '"%d.%m.%Y %H:%M:%S"')
            .replace("timezone: America/New_York", "timezone: Europe/Berlin")
        )
        symbology = (
            drafted["symbology"]
            .replace("multiplier:", "multiplier: 25.0")
            .replace("tick_size:", "tick_size: 0.5")
            .replace("venue:", "venue: EUREX")
            .replace("currency:", "currency: EUR")
        )
        config = drafted["config"].replace("n_paths: 2000", "n_paths: 200")
        status, out = finish_page(
            {
                "token": Upload(value=token_of(page)),
                "mapping": Upload(value=mapping),
                "symbology": Upload(value=symbology),
                "config": Upload(value=config),
            },
            scratch,
        )
        assert status == 200
        assert "SUPPRESSED" in out, "no trials were declared, so D004 must suppress the verdict"

    def test_an_unknown_token_is_a_refusal_not_a_traceback(self, scratch: Scratch) -> None:
        status, page = finish_page({"token": Upload(value="not-a-token")}, scratch)
        assert status == 400
        assert "expired" in page

    def test_an_empty_file_box_is_named(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        status, out = finish_page(
            {"token": Upload(value=token_of(page)), "mapping": Upload(value="  ")}, scratch
        )
        assert status == 400
        assert "mapping.yaml" in out

    def test_an_unusable_configuration_shows_the_reason(self, scratch: Scratch) -> None:
        page = setup_page(upload(), scratch)[1]
        drafted = boxes(page)
        status, out = finish_page(
            {
                "token": Upload(value=token_of(page)),
                "mapping": Upload(value=drafted["mapping"]),
                "symbology": Upload(value=drafted["symbology"]),
                "config": Upload(value=drafted["config"]),
            },
            scratch,
        )
        assert status == 400
        assert "Error" in out or "error" in out


class TestScratch:
    def test_a_stored_log_keeps_its_own_name(self, scratch: Scratch) -> None:
        """D042 puts the name in the provenance, so a generated one would give
        a report naming a file that never existed."""
        token = scratch.store("janeiro.csv", b"a,b\n1,2\n")
        stored = scratch.log_of(token)
        assert stored is not None and stored.name == "janeiro.csv"

    def test_a_filename_carrying_a_path_is_reduced_to_its_leaf(self, scratch: Scratch) -> None:
        token = scratch.store("../../etc/trades.csv", b"a\n1\n")
        stored = scratch.log_of(token)
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
        token = store.store("a.csv", b"a\n1\n")
        folder = store.folder_of(token)
        assert folder is not None
        store.close()
        store.close()
        assert not folder.exists()


class TestStillNoCalculationInTheInterface:
    """``05``'s permanent rule, against the new module too."""

    def test_the_setup_flow_imports_no_core_module(self) -> None:
        offenders = [
            f"{path.name}:{node.lineno}"
            for path in sorted(UI.glob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("qvalid.core")
        ]
        assert offenders == []

    def test_the_drafts_module_computes_nothing_either(self) -> None:
        """It formats what the adapters observed. Arithmetic here would be a
        second implementation of a number the report already states."""
        tree = ast.parse((UI.parents[0] / "drafts.py").read_text(encoding="utf-8"))
        arithmetic = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div | ast.Mult | ast.Sub)
        ]
        assert arithmetic == []

    def test_the_header_is_read_but_the_values_are_not(self, scratch: Scratch) -> None:
        """The mapping draft comes from the header alone, per D060, and the
        page must not have needed a second pass over the rows to build it."""
        with LOG.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        assert boxes(setup_page(upload(), scratch)[1])["mapping"] == mapping_draft(
            header, source_name=LOG.name
        )


def test_the_landing_page_offers_the_guided_route() -> None:
    page = form_page()
    assert 'action="/setup"' in page
    assert "No configuration yet" in page


def test_the_run_config_draft_names_the_two_files_beside_it() -> None:
    text = run_config_draft(mapping_path="mapping.yaml", symbology_path="symbology.yaml")
    assert "mapping_path: mapping.yaml" in text
    assert "symbology_path: symbology.yaml" in text


def test_symbology_draft_is_empty_of_symbols_when_there_are_none() -> None:
    assert symbology_draft((), source_name="x.csv").strip().endswith("symbols:")
