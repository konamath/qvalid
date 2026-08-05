"""D057. The interface, tested without binding a port.

``04`` forbids a test that depends on the network, and a local server is still
a socket. The split that makes this testable is the one v0.7 used for FRED:
everything that decides lives in ``ui/pages.py`` and takes a mapping, and
``ui/server.py`` only moves bytes.

``TestNoCalculationLeaksIn`` is the constraint ``05`` calls permanent, enforced
by reading the syntax tree rather than by remembering.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from qvalid.ui.pages import FIELDS, form_page, run_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = FIXTURES / "trades_long.csv"
CONFIG = FIXTURES / "run_config_full.yaml"
UI = Path(__file__).resolve().parents[2] / "src" / "qvalid" / "ui"


class TestTheFormComesBack:
    def test_the_empty_form_is_a_complete_document(self) -> None:
        page = form_page()
        assert page.startswith("<!doctype html>")
        assert page.rstrip().endswith("</html>")
        for name, label, _ in FIELDS:
            assert f'name="{name}"' in page
            assert label in page

    def test_a_refusal_keeps_what_was_typed(self) -> None:
        """Losing the paths on every refusal is how a form becomes unusable."""
        page = form_page({"log": "/tmp/mine.csv"}, error="something")
        assert 'value="/tmp/mine.csv"' in page

    def test_the_reason_is_escaped(self) -> None:
        """The reason carries a path the person typed, so it is untrusted text."""
        page = form_page(error="<script>alert(1)</script>")
        assert "<script>" not in page
        assert "&lt;script&gt;" in page

    def test_a_typed_path_is_escaped_too(self) -> None:
        page = form_page({"log": '"><script>alert(1)</script>'})
        assert "<script>" not in page


class TestARunReturnsTheReportItself:
    def test_a_good_run_returns_the_report(self) -> None:
        """The result page **is** ``report/html.py``'s output, not a second renderer."""
        status, page = run_page({"log": str(LOG), "config": str(CONFIG)})
        assert status == 200
        assert "Evidence panel" in page
        assert "No aggregate grade is reported" in page

    def test_the_suppressed_verdict_survives_the_interface(self) -> None:
        """``02`` section 7. An interface that dropped this would undo the project."""
        _, page = run_page({"log": str(LOG), "config": str(CONFIG)})
        assert "SUPPRESSED" in page or "NOT_REQUESTED" in page


class TestRefusalsAreShownRatherThanRaised:
    @pytest.mark.parametrize("field", ["log", "config"])
    def test_a_blank_field_is_refused_with_the_form(self, field: str) -> None:
        values = {"log": str(LOG), "config": str(CONFIG)} | {field: "  "}
        status, page = run_page(values)
        assert status == 400
        assert "missing" in page

    def test_a_path_that_is_not_there_names_which_one(self) -> None:
        status, page = run_page({"log": "/no/such/log.csv", "config": str(CONFIG)})
        assert status == 400
        assert "no trade log at" in page
        assert "/no/such/log.csv" in page

    def test_a_refused_configuration_shows_the_reason_not_a_traceback(self, tmp_path: Path) -> None:
        broken = tmp_path / "run.yaml"
        broken.write_text("seed: not_a_number\n", encoding="utf-8")
        status, page = run_page({"log": str(LOG), "config": str(broken)})
        assert status == 400
        assert "SchemaError" in page
        assert "Traceback" not in page

    def test_a_refusal_never_returns_a_partial_report(self, tmp_path: Path) -> None:
        """``02`` section 7: absence is never approval, and half a page is the worst form."""
        broken = tmp_path / "run.yaml"
        broken.write_text("seed: not_a_number\n", encoding="utf-8")
        _, page = run_page({"log": str(LOG), "config": str(broken)})
        assert "Evidence panel" not in page


class TestNoCalculationLeaksIn:
    """``05``: the interface calls the public API and renders. Nothing else."""

    def test_the_interface_imports_no_core_module(self) -> None:
        """Importing ``core`` would be the first step of computing something here.

        The interface is allowed the pipeline and the report layer, which are
        the public API and the renderer. Reaching past them into ``core`` is
        how logic starts appearing in a layer that no numerical test guards.
        """
        offenders = [
            f"{path.name}:{node.lineno} imports {node.module}"
            for path in sorted(UI.glob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("qvalid.core")
        ]
        assert offenders == [], (
            f"the interface reached into core at {offenders}. It may use "
            "qvalid.pipeline and qvalid.report only. See 05 and D057."
        )

    def test_the_interface_contains_no_arithmetic_on_report_values(self) -> None:
        """A number formatted differently here is a number that disagrees with the CLI."""
        source = (UI / "pages.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        operations = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div | ast.Mult | ast.Pow | ast.Sub)
        ]
        assert operations == [], f"arithmetic in the interface at lines {operations}"


class TestTheServerIsOnlyASocket:
    def test_it_binds_the_loopback_address_and_not_every_interface(self) -> None:
        """The tool reads any path it is given, so a reachable server reads any file.

        Nothing here authenticates anyone. Binding to 127.0.0.1 is what makes
        that acceptable rather than negligent, so it is pinned rather than left
        to whoever edits the file next.
        """
        source = (UI / "server.py").read_text(encoding="utf-8")
        assert '"127.0.0.1"' in source
        assert '"0.0.0.0"' not in source

    def test_the_body_it_will_read_is_bounded(self) -> None:
        from qvalid.ui.server import _MAX_BODY_BYTES

        assert 0 < _MAX_BODY_BYTES <= 1_000_000
