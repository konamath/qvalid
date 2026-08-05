"""The interface, as pure functions from a request to a page. See D057.

``05`` names one permanent constraint for this layer: **no calculation here**.
The interface collects a configuration, calls the public API, and renders the
:class:`~qvalid.report.model.ValidationReport`. Any arithmetic that appeared
here would be debt that makes the command line and the interface disagree, and
it would sit outside the test suite that guards every other number.

Two things follow from that, and they are the whole design:

The result page **is** the report. ``report/html.py`` already produces a
complete self contained document, so this module does not render results at
all: it renders a form, and hands back what the report layer produced.

And the request handling is separated from the socket, exactly as
``adapters/market.py`` separates parsing from fetching. Everything here takes a
mapping of submitted fields and returns a page, so the interface is testable
without binding a port, which ``04`` requires of every test.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from pathlib import Path

from qvalid.exceptions import QvalError
from qvalid.pipeline import run_validation
from qvalid.report.html import render_html

__all__ = ["FIELDS", "form_page", "run_page"]

FIELDS: tuple[tuple[str, str, str], ...] = (
    ("log", "Trade log", "CSV exported from your platform"),
    ("config", "Run configuration", "YAML, the one whose hash enters the report"),
)
"""Name, label and hint of every field the form collects.

Two paths and nothing else. Every parameter that changes a number lives in the
configuration file already, and offering to override them here would put the
same decision in two places, one of which is not versioned and does not enter
the provenance hash. See D016.
"""

_STYLE = """
:root { color-scheme: light dark }
body { font-family: Georgia, serif; max-width: 46rem; margin: 3rem auto;
       padding: 0 1.5rem; line-height: 1.6 }
h1 { font-size: 1.6rem; margin-bottom: .2rem }
p.sub { color: #6a6a6a; margin-top: 0 }
label { display: block; margin-top: 1.4rem; font-weight: bold }
small { display: block; color: #6a6a6a; font-weight: normal; margin-bottom: .3rem }
input[type=text] { width: 100%; padding: .5rem; font-family: monospace; font-size: .95rem }
button { margin-top: 1.8rem; padding: .6rem 1.4rem; font-size: 1rem }
.error { border-left: 3px solid #a33; padding: .6rem 1rem; margin-top: 1.5rem }
.error code { display: block; margin-top: .4rem; white-space: pre-wrap }
footer { margin-top: 3rem; color: #6a6a6a; font-size: .9rem }
"""


def form_page(values: Mapping[str, str] | None = None, error: str | None = None) -> str:
    """Render the form, redisplaying what was typed and what went wrong.

    Parameters
    ----------
    values : mapping of str to str, optional
        What the person submitted last time, so a refusal does not cost them
        the paths they typed.
    error : str or None, optional
        A refusal to show above the form.

    Returns
    -------
    str
        A complete HTML document.
    """
    filled = dict(values or {})
    rows = "".join(
        f"<label>{escape(label)}<small>{escape(hint)}</small>"
        f'<input type="text" name="{name}" value="{escape(filled.get(name, ""))}" '
        f'placeholder="path to the file" required></label>'
        for name, label, hint in FIELDS
    )
    warning = (
        f'<div class="error"><strong>The run was refused.</strong>'
        f"<code>{escape(error)}</code></div>"
        if error
        else ""
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>qvalid</title><meta name='viewport' "
        "content='width=device-width, initial-scale=1'>"
        f"<style>{_STYLE}</style></head><body><main>"
        "<h1>qvalid</h1>"
        "<p class='sub'>Point at a trade log and a run configuration. "
        "Every parameter that changes a number lives in the configuration.</p>"
        f"{warning}"
        f'<form method="post" action="/run">{rows}'
        "<button type=submit>Validate</button></form>"
        "<footer>The report opens in place. Nothing is written to disk and "
        "nothing leaves this machine.</footer>"
        "</main></body></html>"
    )


def run_page(values: Mapping[str, str]) -> tuple[int, str]:
    """Validate what was submitted and return the report, or the form with a reason.

    Parameters
    ----------
    values : mapping of str to str
        Submitted fields, keyed by the names in :data:`FIELDS`.

    Returns
    -------
    tuple of (int, str)
        HTTP status and a complete HTML document.

    Notes
    -----
    A missing file is answered with 400 and the form, not with a traceback. The
    person mistyped a path; that is not an error in the sense the report means,
    and showing them a stack trace would teach them nothing about which path
    was wrong.

    A :class:`~qvalid.exceptions.QvalError` is the tool refusing the data, and
    is shown the same way for the same reason. What it must **not** do is
    become a partial report: ``02`` section 7 is clear that absence is never
    approval, and a half rendered page is the worst version of that.
    """
    missing = [label for name, label, _ in FIELDS if not values.get(name, "").strip()]
    if missing:
        return 400, form_page(values, f"missing: {', '.join(missing)}")

    log = Path(values["log"].strip()).expanduser()
    config = Path(values["config"].strip()).expanduser()
    for path, label in ((log, "trade log"), (config, "run configuration")):
        if not path.is_file():
            return 400, form_page(values, f"no {label} at {path}")

    try:
        run = run_validation(log, config)
    except QvalError as exc:
        return 400, form_page(values, f"{type(exc).__name__}: {exc}")
    return 200, render_html(run.report, charts=run.charts)
