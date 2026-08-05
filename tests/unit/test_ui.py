"""D057 and D059. The interface, tested without binding a port.

``04`` forbids a test that depends on the network, and a local server is still
a socket. The split that makes this testable is the one v0.7 used for FRED:
everything that decides lives in ``ui/pages.py`` and ``ui/upload.py`` and takes
plain values, and ``ui/server.py`` only moves bytes.

``TestNoCalculationLeaksIn`` is the constraint ``05`` calls permanent, enforced
by reading the syntax tree rather than by remembering.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from qvalid.ui.pages import CONFIG_FIELD, LOG_FIELD, form_page, run_page
from qvalid.ui.upload import Upload, parse_multipart

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = FIXTURES / "trades_long.csv"
CONFIG = FIXTURES / "run_config_full.yaml"
UI = Path(__file__).resolve().parents[2] / "src" / "qvalid" / "ui"


def submission(
    *, filename: str = "trades_long.csv", config: str = str(CONFIG)
) -> dict[str, Upload]:
    """A well formed submission: the log as bytes, the configuration as a path."""
    return {
        LOG_FIELD[0]: Upload(filename=filename, content=LOG.read_bytes()),
        CONFIG_FIELD[0]: Upload(value=config),
    }


class TestTheMultipartBodyIsParsedByTheStandardLibrary:
    """D059. Boundaries, quoting and encodings are ``email``'s problem, not ours."""

    def build(self, parts: str, boundary: str = "----X") -> tuple[bytes, str]:
        return parts.encode("utf-8"), f"multipart/form-data; boundary={boundary}"

    def test_a_file_part_keeps_its_bytes_and_its_name(self) -> None:
        body, content_type = self.build(
            "------X\r\n"
            'Content-Disposition: form-data; name="log"; filename="trades.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
            "id,pnl\r\nT1,10\r\n"
            "\r\n------X--\r\n"
        )
        parsed = parse_multipart(body, content_type)
        assert parsed["log"].is_file
        assert parsed["log"].filename == "trades.csv"
        assert parsed["log"].content == b"id,pnl\r\nT1,10\r\n"

    def test_a_filename_with_a_space_survives(self) -> None:
        """The commonest real filename, and the commonest hand rolled parser bug."""
        body, content_type = self.build(
            "------X\r\n"
            'Content-Disposition: form-data; name="log"; filename="my trades 2026.csv"\r\n\r\n'
            "a\r\n"
            "\r\n------X--\r\n"
        )
        assert parse_multipart(body, content_type)["log"].filename == "my trades 2026.csv"

    def test_a_text_part_is_stripped_and_not_a_file(self) -> None:
        body, content_type = self.build(
            "------X\r\n"
            'Content-Disposition: form-data; name="config"\r\n\r\n'
            "  /some/path.yaml  "
            "\r\n------X--\r\n"
        )
        field = parse_multipart(body, content_type)["config"]
        assert field.value == "/some/path.yaml"
        assert not field.is_file

    def test_a_body_that_is_not_multipart_yields_nothing_rather_than_raising(self) -> None:
        """Something that is not our form is not an event worth a traceback."""
        assert parse_multipart(b"log=x&config=y", "application/x-www-form-urlencoded") == {}
        assert parse_multipart(b"", "multipart/form-data; boundary=----X") == {}


class TestTheFormComesBack:
    def test_the_empty_form_offers_a_file_input_and_a_path(self) -> None:
        page = form_page()
        assert page.startswith("<!doctype html>")
        assert 'type="file"' in page
        assert 'enctype="multipart/form-data"' in page
        assert f'name="{CONFIG_FIELD[0]}"' in page

    def test_a_refusal_keeps_the_typed_path(self) -> None:
        """Losing it on every refusal is how a form becomes unusable.

        Only the path can be kept. A browser will not let a page refill a file
        input, and that is deliberate on the browser's part.
        """
        page = form_page({CONFIG_FIELD[0]: "/tmp/run.yaml"}, error="something")
        assert 'value="/tmp/run.yaml"' in page

    @pytest.mark.parametrize("hostile", ["<script>alert(1)</script>", '"><img src=x onerror=1>'])
    def test_untrusted_text_is_escaped(self, hostile: str) -> None:
        """The reason and the path both carry text the person typed."""
        assert "<script>" not in form_page(error=hostile)
        assert "<img" not in form_page({CONFIG_FIELD[0]: hostile})


class TestARunReturnsTheReportItself:
    def test_an_uploaded_log_produces_the_report(self) -> None:
        """The result page **is** ``report/html.py``'s output, not a second renderer."""
        status, page = run_page(submission())
        assert status == 200
        assert "Evidence panel" in page
        assert "No aggregate grade is reported" in page

    def test_the_provenance_names_the_uploaded_file_and_not_a_temporary_one(self) -> None:
        """D042 and D059. Provenance for a file that never existed is worse than none."""
        _, page = run_page(submission(filename="janeiro e fevereiro.csv"))
        assert "janeiro e fevereiro.csv" in page
        assert "quantify-" not in page

    def test_a_filename_carrying_a_path_is_reduced_to_its_leaf(self) -> None:
        """A filename is untrusted text from another machine, never a path."""
        status, page = run_page(submission(filename="../../etc/trades.csv"))
        assert status == 200
        assert "../.." not in page

    def test_the_suppressed_verdict_survives_the_interface(self) -> None:
        """``02`` section 7. An interface that dropped this would undo the project."""
        _, page = run_page(submission())
        assert "SUPPRESSED" in page or "NOT_REQUESTED" in page


class TestRefusalsAreShownRatherThanRaised:
    def test_no_file_chosen_is_refused(self) -> None:
        fields = submission()
        fields[LOG_FIELD[0]] = Upload()
        status, page = run_page(fields)
        assert status == 400
        assert "choose a trade log" in page

    def test_an_empty_file_is_refused_by_name(self) -> None:
        fields = submission()
        fields[LOG_FIELD[0]] = Upload(filename="empty.csv", content=b"")
        status, page = run_page(fields)
        assert status == 400
        assert "empty.csv" in page

    def test_a_blank_configuration_path_is_refused(self) -> None:
        status, page = run_page(submission(config="   "))
        assert status == 400
        assert "missing" in page

    def test_a_configuration_that_is_not_there_names_the_path(self) -> None:
        status, page = run_page(submission(config="/no/such/run.yaml"))
        assert status == 400
        assert "/no/such/run.yaml" in page

    def test_a_refused_configuration_shows_the_reason_not_a_traceback(self, tmp_path: Path) -> None:
        broken = tmp_path / "run.yaml"
        broken.write_text("seed: not_a_number\n", encoding="utf-8")
        status, page = run_page(submission(config=str(broken)))
        assert status == 400
        assert "SchemaError" in page
        assert "Traceback" not in page

    def test_a_refusal_never_returns_a_partial_report(self, tmp_path: Path) -> None:
        """``02`` section 7: absence is never approval, and half a page is the worst form."""
        broken = tmp_path / "run.yaml"
        broken.write_text("seed: not_a_number\n", encoding="utf-8")
        _, page = run_page(submission(config=str(broken)))
        assert "Evidence panel" not in page

    def test_nothing_is_left_behind_on_disk(self) -> None:
        """The uploaded bytes live in a temporary directory that closes with the run."""
        import tempfile

        before = set(Path(tempfile.gettempdir()).glob("quantify-*"))
        run_page(submission())
        assert set(Path(tempfile.gettempdir()).glob("quantify-*")) == before


class TestNoCalculationLeaksIn:
    """``05``: the interface calls the public API and renders. Nothing else."""

    def test_the_interface_imports_no_core_module(self) -> None:
        """Importing ``core`` would be the first step of computing something here."""
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

    def test_the_interface_never_reaches_inside_the_report(self) -> None:
        """A number read out of the report here is a number formatted twice.

        The first version of this test banned every ``BinOp`` and immediately
        flagged ``Path(scratch) / filename``, which is a path join. Banning an
        operator catches the syntax and misses the point. What the constraint
        of ``05`` actually forbids is the interface **reading values out of**
        the report instead of handing it whole to the renderer, and those are
        the attributes it would have to touch to do that.
        """
        source = (UI / "pages.py").read_text(encoding="utf-8")
        reached = [
            attribute
            for attribute in (".payload", ".panel", ".provenance", ".sections_run", ".entry(")
            if attribute in source
        ]
        assert reached == [], (
            f"the interface reached into the report through {reached}. It passes the "
            "report whole to render_html and reads nothing out of it. See 05 and D057."
        )


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
        """An unbounded read is how a local server exhausts the machine's memory."""
        from qvalid.ui.server import _MAX_BODY_BYTES

        assert 0 < _MAX_BODY_BYTES <= 256 * 1024 * 1024

    def test_the_stop_instruction_names_the_key_a_mac_has(self) -> None:
        """The first person to read the old wording could not find the key.

        Asserted on the string the person sees, not on the source text: the
        first version of this test read the file and failed on ``\\u2303``,
        which **is** the symbol once Python has read the literal. A test of the
        source is a test of how the character was spelled.
        """
        from qvalid.ui.server import STOP_HINT

        assert "\u2303C" in STOP_HINT
        assert "control" in STOP_HINT
