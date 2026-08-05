"""Regenerate the reference report that ``test_reproducibility.py`` compares against.

Run deliberately, never reflexively::

    python tests/regenerate_expected.py

A failing reproducibility test means either a number moved or the reference is
stale. Running this script answers "the reference is stale" without checking,
so the only honest use is after reading the diff the failure printed and
deciding the new numbers are the right ones.
"""

from __future__ import annotations

from pathlib import Path

from qvalid.pipeline import run_validation
from qvalid.report.json import write_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FROZEN_TIMESTAMP = "2026-08-05T00:00:00Z"


def main() -> None:
    """Write ``tests/fixtures/expected_report.json`` with the timestamp held fixed."""
    run = run_validation(
        FIXTURES / "trades_long.csv",
        FIXTURES / "run_config_full.yaml",
        executed_at=FROZEN_TIMESTAMP,
    )
    target = FIXTURES / "expected_report.json"
    write_json(run.report, target)
    print(f"wrote {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
