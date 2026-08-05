"""Check that the example's own output matches the committed reference.

``tests/unit/test_reproducibility.py`` calls the pipeline directly, which
proves the library reproduces. This proves the **example** reproduces, which is
what ``05`` v1.0 actually asks: someone clones the repository, runs one
command, and gets the report the README describes.

The difference matters. The example writes its own files, from its own
directory, through the same code path a user takes. A regression in
``examples/validate_full.py`` alone would leave the test suite green and the
front page wrong.

What "matches" means is defined once in ``tests/comparison.py`` and explained
by D049: numbers to a declared precision three orders tighter than the report
renders, everything else exactly.

Run after the example::

    python examples/validate_full.py
    python tests/check_example_output.py

Exits non zero and names the values that moved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comparison import RELATIVE_TOLERANCE, moved_values

ROOT = Path(__file__).resolve().parents[1]
PRODUCED = ROOT / "examples" / "output" / "report.json"
REFERENCE = ROOT / "tests" / "fixtures" / "expected_report.json"

VOLATILE = (".provenance.executed_at",)
"""The one leaf ``05`` allows to vary between two runs."""


def main() -> int:
    """Compare and report. Returns the process exit code."""
    if not PRODUCED.is_file():
        print(f"missing {PRODUCED.relative_to(ROOT)}; run examples/validate_full.py first")
        return 2

    produced = json.loads(PRODUCED.read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    moved = moved_values(produced, reference, ignore=VOLATILE)

    if moved:
        print(f"the example no longer reproduces the reference; {len(moved)} value(s) moved:")
        for line in moved[:20]:
            print(f"  {line}")
        return 1

    print(f"the example reproduces the reference to {RELATIVE_TOLERANCE:.0e} relative")
    return 0


if __name__ == "__main__":
    sys.exit(main())
