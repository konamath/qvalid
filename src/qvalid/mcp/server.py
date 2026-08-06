"""The stdio loop, and nothing else. See D075.

One newline delimited JSON message per line, in and out. Kept apart from
:mod:`qvalid.mcp.protocol` for the reason ``adapters/market.py`` keeps the
socket apart from the parsing: everything above this file is testable with
bytes, and this file is the only part that touches a stream.

Not covered by tests, deliberately, exactly as :func:`qvalid.ui.server.serve` is
not: everything it calls is, and a test here would be testing ``sys.stdin``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from qvalid.mcp.protocol import handle

__all__ = ["serve_stdio"]


def serve_stdio(cache_root: str | Path) -> None:  # pragma: no cover
    """Read requests from stdin and write responses to stdout until it closes.

    Nothing is printed to stdout except responses. A stray print here corrupts
    the stream and the client sees a parse error it cannot attribute, so
    anything to say goes to stderr.
    """
    print(f"quantify mcp: serving {Path(cache_root)} read only", file=sys.stderr, flush=True)
    for line in sys.stdin.buffer:
        payload = line.strip()
        if not payload:
            continue
        answer = handle(payload, cache_root)
        if answer is not None:
            sys.stdout.buffer.write(answer + b"\n")
            sys.stdout.buffer.flush()
