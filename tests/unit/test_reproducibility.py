"""The v1.0 criterion of ``05``, as a test rather than as a claim.

``05`` opens with the rule that a version closes when the criterion is
verifiable by command. The v1.0 criterion is that someone clones the repository
and reproduces the example, so the thing to verify is not that two runs on this
machine agree, which ``test_report.py`` already pins, but that a run today
produces the same bytes as the run that generated the numbers printed in the
README.

The reference lives in ``tests/fixtures/expected_report.json`` and the
timestamp is injected, so no field has to be excluded for varying. When this
test fails, either a number moved or the reference is stale, and both are
things a person should be told about rather than left to discover from a report
they trusted.

Regenerate deliberately, never reflexively::

    python tests/regenerate_expected.py

What "matches" means is defined in ``tests/comparison.py``. It is **not** byte
for byte, and D049 records why with the measurement: two of the report's 144
values move by one or two units in the last place when numpy and scipy change
version, and both are reductions whose summation order the library chooses. The
criterion is derived from the six significant figures the report renders rather
than tuned to pass.

Byte for byte survives where it is achievable, and is still checked there:
``test_report.py::TestByteForByte`` pins that two runs in **one** environment
produce identical bytes. That is the claim about the seed governing everything,
and it is unconditional.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from qvalid.pipeline import run_validation
from qvalid.report.json import report_to_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comparison import RELATIVE_TOLERANCE, moved_values

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
EXPECTED = FIXTURES / "expected_report.json"
FROZEN_TIMESTAMP = "2026-08-05T00:00:00Z"


@pytest.fixture(scope="module")
def produced() -> str:
    run = run_validation(
        FIXTURES / "trades_long.csv",
        FIXTURES / "run_config_full.yaml",
        executed_at=FROZEN_TIMESTAMP,
    )
    return report_to_json(run.report)


class TestTheExampleReproduces:
    def test_the_report_matches_the_committed_reference(self, produced: str) -> None:
        """The whole v1.0 criterion, in one assertion. See D049 for the precision."""
        moved = moved_values(json.loads(produced), json.loads(EXPECTED.read_text("utf-8")))
        assert moved == [], (
            f"the example no longer reproduces the committed reference at "
            f"{RELATIVE_TOLERANCE:.0e} relative; {len(moved)} value(s) moved: {moved[:10]}"
        )

    def test_every_string_in_the_report_still_matches_exactly(self, produced: str) -> None:
        """Rounding explains a number moving. It never explains a word moving.

        The reason an absent section gives, the regime identifier, the chosen
        period: a difference in any of those is a behaviour change wearing the
        costume of a floating point difference, so the comparison refuses to
        apply a tolerance to them.
        """
        left, right = json.loads(produced), json.loads(EXPECTED.read_text("utf-8"))
        moved = moved_values(left, right, ignore=(".provenance.executed_at",))
        assert not [line for line in moved if "relative" not in line]

    def test_a_string_path_works_as_the_signature_promises(self) -> None:
        """D046. The signature says ``str | Path`` and nothing ever passed a string.

        Every caller in the repository happens to hold a ``Path``, so the
        string half of the public signature was never executed. It crashed on
        ``log_path.name``, and only mypy saw it.
        """
        run = run_validation(
            str(FIXTURES / "trades_long.csv"),
            str(FIXTURES / "run_config_full.yaml"),
            executed_at=FROZEN_TIMESTAMP,
        )
        assert run.report.provenance.input_name == "trades_long.csv"
        assert (
            moved_values(
                json.loads(report_to_json(run.report)), json.loads(EXPECTED.read_text("utf-8"))
            )
            == []
        )

    def test_the_reference_carries_no_absolute_path(self) -> None:
        """D042. A path from the machine that produced it cannot reproduce elsewhere."""
        reference = json.loads(EXPECTED.read_text(encoding="utf-8"))
        assert reference["provenance"]["input_name"] == "trades_long.csv"
        assert "/" not in reference["provenance"]["input_name"]

    def test_the_numbers_quoted_in_the_readme_are_the_numbers_produced(self, produced: str) -> None:
        """A README that drifts from the tool is worse than no README.

        Every figure the front page states about the example is pinned here, so
        the page cannot quietly stop being true.
        """
        report = json.loads(produced)
        panel = {entry["name"]: entry for entry in report["panel"]}
        calendar = panel["calendar_metrics"]["payload"]
        assert round(calendar["sharpe_sqrt_q"], 2) == -0.91
        assert round(calendar["sharpe_ci_low"], 2) == -2.58
        assert round(calendar["sharpe_ci_high"], 2) == 0.76
        assert round(panel["drawdown_distribution"]["payload"]["observed_quantile"], 2) == 0.68
        assert round(panel["risk_of_ruin"]["payload"]["probability"], 2) == 0.21
        assert panel["deflated_sharpe"]["status"] == "NOT_REQUESTED"
        assert panel["verdict"]["status"] == "SUPPRESSED"
        assert report["provenance"]["seed"] == 20260804
        assert report["provenance"]["n_replications"] == 3000

    def test_the_run_is_still_free_of_the_network(self) -> None:
        """``04`` forbids a test that depends on the network, and so does the example.

        The example reads two files from ``tests/fixtures`` and nothing else.
        If it ever grew a download, this suite would start failing on an
        aeroplane and the v1.0 criterion would quietly stop holding.
        """
        import socket

        original = socket.socket

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("the example opened a socket")

        socket.socket = refuse  # type: ignore[assignment,misc]
        try:
            run_validation(
                FIXTURES / "trades_long.csv",
                FIXTURES / "run_config_full.yaml",
                executed_at=FROZEN_TIMESTAMP,
            )
        finally:
            socket.socket = original  # type: ignore[misc]
