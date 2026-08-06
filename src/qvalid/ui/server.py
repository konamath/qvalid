"""The socket, and nothing else. See D057.

Every decision lives in :mod:`qvalid.ui.pages`, which takes a mapping and
returns a page. This module binds a port and moves bytes, so it is the only
part of the interface a test cannot reach without opening a socket, and it is
deliberately short enough to read in one go.

The standard library rather than a framework, on purpose. FastAPI and uvicorn
would do the same job and would be charged to everyone who installs the
package for its API, which is the mistake D044 found already sitting in the
dependency list for nine versions. The precedent is D030, which hand rolled SVG
rather than take matplotlib.

Declared limitation: :class:`http.server.HTTPServer` is single threaded and
serves one request at a time, so a long run blocks the next click. ``05`` puts
this interface in a stage that prioritises building speed over polish, and a
queue is what stage two is for. Until then a second click waits, which is
honest and visible rather than silently interleaved.
"""

from __future__ import annotations

import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from qvalid.ui.pages import finish_page, form_page, run_page, setup_page
from qvalid.ui.scratch import Scratch
from qvalid.ui.upload import parse_multipart

__all__ = ["serve"]

DEFAULT_PORT = 8765

STOP_HINT = "press \u2303C, the control key, to stop"
"""How to stop it, named the way a Mac keyboard labels the key.

It said "control C" first, and the first person to read it went looking for a
key that a Mac writes as the symbol. A message that confused its first reader
will confuse its second, so the symbol is shown and the word kept beside it."""
_MAX_BODY_BYTES = 64 * 1024 * 1024
"""Sixty four megabytes, which is a trade log of a few million rows.

Larger than the two paths this used to accept, because the log now arrives as
bytes rather than as a path. Bounded anyway: an unbounded read is how a local
server becomes a way to exhaust the machine's memory, and no export of closed
trades reaches this size."""


_SCRATCH = Scratch()
"""One store per process, because guided setup spans two requests.

Module level rather than per handler: :class:`~http.server.BaseHTTPRequestHandler`
is instantiated once per connection, so a store held on the handler would lose
the upload the moment the browser opened a second connection, which it does.
"""


class _Handler(BaseHTTPRequestHandler):
    """Route the paths onto the functions in :mod:`qvalid.ui.pages`."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Serve the form at the root and refuse everything else."""
        if self.path in ("/", "/index.html"):
            self._respond(200, form_page())
        else:
            self._respond(404, form_page(error=f"no page at {self.path}"))

    def do_POST(self) -> None:
        """Dispatch a submission to the page that answers it."""
        if self.path not in ("/run", "/setup", "/finish"):
            self._respond(404, form_page(error=f"no page at {self.path}"))
            return
        declared = int(self.headers.get("Content-Length") or 0)
        if declared > _MAX_BODY_BYTES:
            self._respond(
                413, form_page(error=f"the upload is larger than {_MAX_BODY_BYTES // 1024**2} MB")
            )
            return
        body = self.rfile.read(declared)
        fields = parse_multipart(body, self.headers.get("Content-Type", ""))
        if self.path == "/run":
            status, page = run_page(fields)
        elif self.path == "/setup":
            status, page = setup_page(fields, _SCRATCH)
        else:
            status, page = finish_page(fields, _SCRATCH)
        self._respond(status, page)

    def log_message(self, format: str, *args: object) -> None:
        """Silence the per request line. The person is watching a browser, not a log."""

    def _respond(self, status: int, page: str) -> None:
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = DEFAULT_PORT, *, open_browser: bool = True) -> None:  # pragma: no cover
    """Bind the loopback address and serve until interrupted.

    Loopback and not all interfaces. The tool reads whatever path it is given,
    so a server reachable from the network would be a file reader reachable
    from the network. Nothing here authenticates anyone, and binding to
    ``127.0.0.1`` is what makes that acceptable rather than negligent.

    Not covered by tests, deliberately: everything above it is, and a test that
    bound a port would be testing :mod:`http.server`.
    """
    server = HTTPServer(("127.0.0.1", port), _Handler)
    address = f"http://127.0.0.1:{port}/"
    print(f"Quantify interface on {address}\n{STOP_HINT}")
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
