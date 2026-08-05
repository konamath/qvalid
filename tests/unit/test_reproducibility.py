"""The v1.0 criterion of ``05``, as a test rather than as a claim.

``05`` opens with the rule that a version closes when the criterion is
verifiable by command. The v1.0 criterion is that someone clones the repository
and reproduces the example, so the thing to verify is not that two runs on this
machine agree, which ``test_report.py`` already pins, but that a run today
produces the same bytes as the run that generated the numbers printed in the
README.

The reference lives in ``tests/fixtures/expected_report.json`` and the
timestamp is injected, so the comparison is exact and needs no field to be
excluded. When this test fails, either a number moved or the reference is
stale, and both are things a person should be told about rather than left to
discover from a report they trusted.

Regenerate deliberately, never reflexively::

    python tests/regenerate_expected.py

Cross platform note, see D043: this is verified on Linux with CPython 3.12.
``math.log`` and ``math.exp`` are not required to be correctly rounded and do
differ between C libraries, so the same exactness across macOS and Windows is
asserted by the CI matrix and not by a measurement made here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qvalid.pipeline import run_validation
from qvalid.report.json import report_to_json

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
    def test_the_report_matches_the_committed_reference_byte_for_byte(self, produced: str) -> None:
        """The whole v1.0 criterion, in one assertion."""
        reference = EXPECTED.read_text(encoding="utf-8")
        if produced == reference:
            return
        left = json.loads(produced)
        right = json.loads(reference)
        moved = sorted(_moved_leaves(left, right))
        pytest.fail(
            f"the example no longer reproduces the committed reference; "
            f"{len(moved)} value(s) moved: {moved[:10]}"
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


def _moved_leaves(left: object, right: object, path: str = "") -> list[str]:
    """Every leaf that differs, so a failure names the number instead of the file."""
    if isinstance(left, dict) and isinstance(right, dict):
        out: list[str] = []
        for key in sorted(set(left) | set(right)):
            out += _moved_leaves(left.get(key), right.get(key), f"{path}.{key}")
        return out
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        out = []
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            out += _moved_leaves(a, b, f"{path}[{index}]")
        return out
    return [] if left == right else [f"{path}: {left!r} != {right!r}"]
