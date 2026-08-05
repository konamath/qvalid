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
from urllib.parse import parse_qs

from qvalid.ui.pages import form_page, run_page

__all__ = ["serve"]

DEFAULT_PORT = 8765
_MAX_BODY_BYTES = 64 * 1024
"""A form with two paths cannot be larger; anything bigger is not our form."""


class _Handler(BaseHTTPRequestHandler):
    """Route two paths onto the two functions in :mod:`qvalid.ui.pages`."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Serve the form at the root and refuse everything else."""
        if self.path in ("/", "/index.html"):
            self._respond(200, form_page())
        else:
            self._respond(404, form_page(error=f"no page at {self.path}"))

    def do_POST(self) -> None:
        """Run a validation and return the report."""
        if self.path != "/run":
            self._respond(404, form_page(error=f"no page at {self.path}"))
            return
        length = min(int(self.headers.get("Content-Length") or 0), _MAX_BODY_BYTES)
        submitted = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        status, page = run_page({key: value[0] for key, value in submitted.items()})
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
    print(f"Quantify interface on {address}\npress control C to stop")
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
