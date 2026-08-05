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

import tempfile
from collections.abc import Mapping
from html import escape
from pathlib import Path

from qvalid.exceptions import QvalError
from qvalid.pipeline import run_validation
from qvalid.report.html import render_html
from qvalid.ui.upload import Upload

__all__ = ["form_page", "run_page"]

LOG_FIELD = ("log", "Trade log", "The CSV your platform exported. Drag it in.")
CONFIG_FIELD = (
    "config",
    "Run configuration",
    "Path to the YAML whose hash enters the report",
)
"""The two fields, collected in two different ways, and the asymmetry is the point.

The **log** is uploaded. It comes from wherever the platform dropped it and
changes from run to run, so making the person find and type an absolute path is
the friction this interface exists to remove.

The **configuration** stays a path, and not for want of effort. It names
``symbology_path`` and ``mapping_path`` **relative to itself**, so a YAML
uploaded on its own arrives without the two files it depends on. Beyond that,
D016 makes the configuration versioned provenance rather than something handed
over ad hoc, and a path is the right handle for a file that is supposed to live
somewhere permanent.

Nothing else is collected. Every parameter that changes a number lives in the
configuration already, and offering to override it here would put one decision
in two places, one of which is not versioned and never reaches the report.
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
    log_name, log_label, log_hint = LOG_FIELD
    config_name, config_label, config_hint = CONFIG_FIELD
    rows = (
        f"<label>{escape(log_label)}<small>{escape(log_hint)}</small>"
        f'<input type="file" name="{log_name}" accept=".csv,text/csv" required></label>'
        f"<label>{escape(config_label)}<small>{escape(config_hint)}</small>"
        f'<input type="text" name="{config_name}" '
        f'value="{escape(filled.get(config_name, ""))}" '
        f'placeholder="path to the file" required></label>'
    )
    warning = (
        f'<div class="error"><strong>The run was refused.</strong>'
        f"<code>{escape(error)}</code></div>"
        if error
        else ""
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Quantify</title><meta name='viewport' "
        "content='width=device-width, initial-scale=1'>"
        f"<style>{_STYLE}</style></head><body><main>"
        "<h1>Quantify</h1>"
        "<p class='sub'>Point at a trade log and a run configuration. "
        "Every parameter that changes a number lives in the configuration.</p>"
        f"{warning}"
        f'<form method="post" action="/run" enctype="multipart/form-data">{rows}'
        "<button type=submit>Validate</button></form>"
        "<footer>The report opens in place. Nothing is written to disk and "
        "nothing leaves this machine.</footer>"
        "</main></body></html>"
    )


def run_page(fields: Mapping[str, Upload]) -> tuple[int, str]:
    """Validate what was submitted and return the report, or the form with a reason.

    Parameters
    ----------
    fields : mapping of str to Upload
        Submitted fields. ``log`` carries the uploaded file's bytes and its
        original name; ``config`` carries a path typed by the person.

    Returns
    -------
    tuple of (int, str)
        HTTP status and a complete HTML document.

    Notes
    -----
    The uploaded log is written to a temporary directory **under its original
    name**, and the directory is removed before returning. The name matters
    because D042 puts it in the provenance: writing it under a generated name
    would give the person a report whose provenance names a file that never
    existed. The directory matters because the tool reads from disk and holding
    the bytes in memory would mean a second code path for the same read.

    A missing file, a blank path or a refused configuration is answered with
    400 and the form, not with a traceback. The person mistyped something; that
    is not an error in the sense the report means. What a refusal must **not**
    do is become a partial report: ``02`` section 7 is clear that absence is
    never approval, and half a rendered page is the worst version of that.
    """
    log = fields.get(LOG_FIELD[0], Upload())
    config_text = fields.get(CONFIG_FIELD[0], Upload()).value.strip()
    typed = {CONFIG_FIELD[0]: config_text}

    if not log.is_file or not log.filename:
        return 400, form_page(typed, "choose a trade log to upload")
    if not log.content:
        return 400, form_page(typed, f"the uploaded file {log.filename} is empty")
    if not config_text:
        return 400, form_page(typed, "missing: the path to the run configuration")

    config = Path(config_text).expanduser()
    if not config.is_file():
        return 400, form_page(typed, f"no run configuration at {config}")

    with tempfile.TemporaryDirectory(prefix="quantify-") as scratch:
        # Under the name the browser reported, never under a path from it. A
        # filename is untrusted text, so only its basename is used and only as
        # a leaf inside a directory this process just created.
        uploaded = Path(scratch) / Path(log.filename).name
        uploaded.write_bytes(log.content)
        try:
            run = run_validation(uploaded, config)
        except QvalError as exc:
            return 400, form_page(typed, f"{type(exc).__name__}: {exc}")
        return 200, render_html(run.report, charts=run.charts)
