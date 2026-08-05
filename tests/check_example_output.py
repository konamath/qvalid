"""Check that the example's own output matches the committed reference.

``tests/unit/test_reproducibility.py`` calls the pipeline directly, which
proves the library reproduces. This proves the **example** reproduces, which is
what ``05`` v1.0 actually asks: someone clones the repository, runs one
command, and gets the report the README describes.

The difference matters. The example writes its own files, from its own
directory, through the same code path a user takes. A regression in
``examples/validate_full.py`` alone would leave the test suite green and the
front page wrong.

Run after the example::

    python examples/validate_full.py
    python tests/check_example_output.py

Exits non zero and names the fields that moved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCED = ROOT / "examples" / "output" / "report.json"
REFERENCE = ROOT / "tests" / "fixtures" / "expected_report.json"

#: The one field allowed to differ, and the reason ``05`` states the criterion
#: as "byte for byte except the timestamp".
VOLATILE = ("provenance", "executed_at")


def _leaves(value: object, path: str = "") -> dict[str, object]:
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            out |= _leaves(item, f"{path}.{key}")
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out |= _leaves(item, f"{path}[{index}]")
        return out
    return {path: value}


def main() -> int:
    """Compare and report. Returns the process exit code."""
    if not PRODUCED.is_file():
        print(f"missing {PRODUCED.relative_to(ROOT)}; run examples/validate_full.py first")
        return 2

    produced = json.loads(PRODUCED.read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    for mapping in (produced, reference):
        mapping[VOLATILE[0]].pop(VOLATILE[1], None)

    left, right = _leaves(produced), _leaves(reference)
    moved = sorted(
        key for key in set(left) | set(right) if left.get(key, object()) != right.get(key)
    )
    if moved:
        print(f"the example no longer reproduces the reference; {len(moved)} value(s) moved:")
        for key in moved[:20]:
            print(f"  {key}: {left.get(key)!r} != {right.get(key)!r}")
        return 1

    print(f"the example reproduces the reference exactly, over {len(left)} values")
    return 0


if __name__ == "__main__":
    sys.exit(main())
