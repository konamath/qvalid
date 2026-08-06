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

import csv
import tempfile
from collections.abc import Mapping
from html import escape
from pathlib import Path

from qvalid.adapters.probe import Declarations, SymbolProbe, probe_trade_log, read_declarations
from qvalid.adapters.suggest import suggest_columns
from qvalid.adapters.timeformats import FormatMatch, matching_formats
from qvalid.adapters.tradelog import load_mapping_text
from qvalid.drafts import mapping_draft
from qvalid.exceptions import QvalError
from qvalid.pipeline import run_validation
from qvalid.report.html import render_html
from qvalid.ui.form import MAPPED_FIELDS, build_files, render_form
from qvalid.ui.scratch import Scratch
from qvalid.ui.upload import Upload

__all__ = ["finish_page", "form_page", "run_page", "setup_page"]

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
textarea { width: 100%; font-family: monospace; font-size: .82rem; line-height: 1.45;
           padding: .6rem; white-space: pre; overflow-x: auto }
h2 { font-size: 1.15rem; margin-top: 2.4rem; border-bottom: 1px solid #ddd; padding-bottom: .2rem }
p.hint { color: #6a6a6a; font-size: .9rem; margin: .2rem 0 .6rem }
.scroll { overflow-x: auto; margin-bottom: 1rem }
table.preview { border-collapse: collapse; font-size: .74rem; font-family: monospace }
table.preview th, table.preview td { border: 1px solid #ddd; padding: .18rem .4rem;
                                     white-space: nowrap; text-align: left }
table.fields { border-collapse: collapse; width: 100% }
table.fields th { text-align: left; font-family: monospace; font-weight: normal;
                  padding: .3rem .6rem .3rem 0; white-space: nowrap }
table.fields td { padding: .3rem 0 }
table.fields td.hint { color: #6a6a6a; font-size: .85rem; padding-left: .8rem }
table.fields em { color: #a33; font-style: normal }
select { padding: .35rem; font-size: .9rem; min-width: 14rem }
select.clash { outline: 2px solid #a33 }
input[type=number] { width: 100%; padding: .5rem; font-family: monospace; font-size: .95rem }
fieldset { border: 1px solid #ddd; padding: .2rem 1rem 1rem; margin-top: 1rem }
legend { font-family: monospace; font-weight: bold; padding: 0 .4rem }
label.inline { display: inline-block; margin-right: 1rem; font-weight: normal }
button[disabled] { opacity: .45; cursor: not-allowed }
.keep { border: 1px solid #7a7; padding: .8rem 1rem; margin-bottom: 2rem; font-size: .9rem }
.keep pre { font-size: .74rem; overflow-x: auto; white-space: pre }
.keep summary { cursor: pointer; font-family: monospace; margin-top: .5rem }
"""


def form_page(values: Mapping[str, str] | None = None, error: str | None = None) -> str:
    """Render the landing page: the guided route first, the expert one below.

    The order is the whole point, and it took two people looking at the screen
    to see it. This page used to lead with "point at a trade log **and a run
    configuration**", which asks for three YAML files nobody has the first time,
    and buried the guided route under a rule and the heading "No configuration
    yet?". The first thing a new arrival was shown was the one thing they could
    not do. See D068.

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
        "<p class='sub'>Drop the CSV your platform exported. The next page is filled in from "
        "your own file: each field matched to the column whose name fits, the cost convention "
        "read from the sign of your cost column, the date format read from the column itself. "
        "What it cannot work out, it asks.</p>"
        f"{warning}"
        '<form method="post" action="/setup" enctype="multipart/form-data">'
        f"<label>{escape(log_label)}<small>{escape(log_hint)}</small>"
        f'<input type="file" name="{log_name}" accept=".csv,text/csv" required></label>'
        "<button type=submit>Configure and validate</button></form>"
        "<hr style='margin:3rem 0;border:0;border-top:1px solid #ccc'>"
        "<h2 style='font-size:1.05rem'>Already have the three files?</h2>"
        "<p class='hint'>Point at the log and at the run configuration whose hash enters the "
        "report. This is the route for a run you have done before and want to repeat exactly.</p>"
        f'<form method="post" action="/run" enctype="multipart/form-data">'
        f"<label>{escape(log_label)}<small>{escape(log_hint)}</small>"
        f'<input type="file" name="{log_name}" accept=".csv,text/csv" required></label>'
        f"<label>{escape(config_label)}<small>{escape(config_hint)}</small>"
        f'<input type="text" name="{config_name}" '
        f'value="{escape(filled.get(config_name, ""))}" '
        f'placeholder="path to the file" required></label>'
        "<button type=submit>Validate</button></form>"
        "<footer>The report opens in place. Nothing is written outside a temporary "
        "folder and nothing leaves this machine.</footer>"
        "</main></body></html>"
    )


def _document(title: str, body: str) -> str:
    """Wrap a body in the shell every page of the interface shares."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><meta name='viewport' "
        "content='width=device-width, initial-scale=1'>"
        f"<style>{_STYLE}</style></head><body><main>{body}</main></body></html>"
    )


def _rows_of(path: Path, limit: int = 6) -> tuple[list[str], list[list[str]], list[str]]:
    """Header, first rows for the preview, and the whole exit stamp column.

    The preview is short because it is there to remind the person what their
    columns look like. The stamp column is read in full because
    :func:`~qvalid.adapters.timeformats.matching_formats` gets stronger with
    every row: one stamp cannot separate day first from month first, and a
    column almost always can.
    """
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        body = list(reader)
    return header, body[:limit], [row[0] for row in body if row]


def setup_page(fields: Mapping[str, Upload], scratch: Scratch) -> tuple[int, str]:
    """Draft the configuration as a form, filled in from the file. See D066.

    No arithmetic here, per the permanent constraint in ``05``. The column
    matches come from :mod:`qvalid.adapters.suggest`, the fee sign and sample
    stamps from :mod:`qvalid.adapters.probe`, the timestamp patterns from
    :mod:`qvalid.adapters.timeformats`, and the implied multipliers from the
    probe. This module places them on a page.

    The probe needs a usable mapping to read anything, and the suggested one is
    not usable when a column went unresolved. In that case the form still
    renders, with the contract section saying so, and filling in the columns
    and submitting brings it back populated.
    """
    log = fields.get(LOG_FIELD[0], Upload())
    if not log.is_file or not log.filename:
        return 400, form_page(error="choose a trade log to upload")
    if not log.content:
        return 400, form_page(error=f"the uploaded file {log.filename} is empty")

    token = scratch.store(log.filename, log.content)
    stored = scratch.log_of(token)
    if stored is None:  # pragma: no cover - the store just wrote it
        return 500, form_page(error="the upload could not be stored")
    header, rows, _ = _rows_of(stored)
    if not header:
        return 400, form_page(error=f"{log.filename} has no header row")

    return 200, _document("Quantify setup", _configuration_body(token, stored, header, rows))


def _configuration_body(
    token: str,
    stored: Path,
    header: list[str],
    rows: list[list[str]],
    submitted: Mapping[str, str] | None = None,
    error: str | None = None,
) -> str:
    """Everything the configuration page shows, so a refusal can redisplay it."""
    found = suggest_columns(header)
    declared: Declarations | None = None
    stamps: FormatMatch | None = None
    probes: tuple[SymbolProbe, ...] = ()
    try:
        mapping = load_mapping_text(mapping_draft(header, source_name=stored.name))
        declared = read_declarations(stored, mapping)
        probes = probe_trade_log(stored, mapping)
        with stored.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            index = header.index(mapping.columns["entry_ts"])
            stamps = matching_formats([row[index] for row in reader if len(row) > index])
    except (QvalError, ValueError):
        stamps = matching_formats([])

    warning = (
        f'<div class="error"><strong>The run was refused.</strong>'
        f"<code>{escape(error)}</code></div>"
        if error
        else ""
    )
    # Zero matches is not ten small problems, it is one large one. Saying "no
    # column matched" beside every field and disabling the button leaves the
    # person hunting for the ten things they did wrong, when the thing they did
    # was open the wrong file. See D067.
    if not found.columns:
        warning = (
            '<div class="error"><strong>This does not look like a trade log.</strong>'
            f"<code>Not one of the {len(MAPPED_FIELDS)} fields matched a column in "
            f"{escape(stored.name)}, whose columns are: {escape(', '.join(header[:8]))}"
            f"{'...' if len(header) > 8 else ''}."
            "\n\nA trade log has one row per closed trade, with an instrument, a side, a "
            "quantity, two timestamps, two prices and a profit. If this is a returns series, "
            "an account statement or a matrix of tested configurations, it is a different "
            "kind of file and this page cannot use it.\n\nIf it really is your trade log "
            "and the names are simply unusual, the menus below still work; choose the "
            "columns yourself.</code></div>"
        ) + warning
    return (
        "<h1>Configure the run</h1>"
        f"<p class='sub'>Everything already filled in was read from "
        f"<code>{escape(stored.name)}</code>. Everything empty is a decision the file "
        "cannot make for you.</p>"
        f"{warning}"
        + render_form(
            token=token,
            log_name=stored.name,
            header=header,
            rows=rows,
            suggestion=found,
            declarations=declared,
            stamps=stamps,
            probes=probes,
            submitted=submitted,
        )
        + "<footer>Nothing leaves this machine. The three files this builds are shown "
        "with the report; keep them, or the run is not reproducible.</footer>"
    )


def finish_page(fields: Mapping[str, Upload], scratch: Scratch) -> tuple[int, str]:
    """Assemble the three files from the form, write them, and validate.

    The files are written because the tool reads configuration from disk, and a
    second path that read it from memory would be another thing to keep
    correct. They are shown in full above the report, because under D016 the
    file is the provenance and a person who cannot keep it cannot reproduce the
    number they were just given.
    """
    plain = {name: upload.value for name, upload in fields.items()}
    token = plain.get("token", "").strip()
    folder = scratch.folder_of(token)
    log = scratch.log_of(token)
    if folder is None or log is None:
        return 400, form_page(
            error="that upload has expired; the interface keeps only the most recent few"
        )
    header, rows, _ = _rows_of(log)

    try:
        mapping, symbology, run = build_files(plain)
    except ValueError as exc:
        return 400, _document(
            "Quantify setup", _configuration_body(token, log, header, rows, plain, str(exc))
        )

    for name, text in (("mapping.yaml", mapping), ("symbology.yaml", symbology), ("run.yaml", run)):
        (folder / name).write_text(text, encoding="utf-8")
    try:
        result = run_validation(log, folder / "run.yaml")
    except QvalError as exc:
        return 400, _document(
            "Quantify setup",
            _configuration_body(token, log, header, rows, plain, f"{type(exc).__name__}: {exc}"),
        )

    kept = "".join(
        f"<details><summary>{escape(name)}</summary><pre>{escape(text)}</pre></details>"
        for name, text in (
            ("mapping.yaml", mapping),
            ("symbology.yaml", symbology),
            ("run.yaml", run),
        )
    )
    banner = (
        f"<div class='keep'><strong>Keep these three files.</strong> They are what makes the "
        f"report below reproducible; without them it is a number without provenance. "
        f"See D016.{kept}</div>"
    )
    return 200, render_html(result.report, charts=result.charts, prologue=banner)


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
