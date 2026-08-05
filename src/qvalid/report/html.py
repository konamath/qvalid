"""Self contained HTML rendering of a validation report.

Self contained means exactly that: no stylesheet link, no script source, no
image URL. Everything, including the charts, is inline. A report that fetches
anything is a report that renders differently next year, and ``01`` asks for an
artefact that can be read on its own.

The rendering reads the same dictionary that :mod:`qvalid.report.json` produces,
so the two cannot disagree about a number. The charts are the only thing this
module adds, and they come from :mod:`qvalid.report.svg`, which is deterministic
by construction.

Layout follows the panel, not a narrative. Sections that did not run appear in
the same list as sections that did, marked with why. Putting the absent ones in
a footnote, or omitting them, would produce the exact reading ``02`` section 7
forbids: a reader scanning for red flags and finding none because none were
printed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from qvalid.report.json import report_to_dict
from qvalid.report.model import EvidenceStatus, ValidationReport
from qvalid.report.svg import escape

__all__ = ["render_html", "write_html"]

_STYLE = """
:root { --ink:#1a1a1a; --muted:#6b6b6b; --rule:#d8d8d8; --accent:#1f4e79; --warn:#a33; }
* { box-sizing:border-box; }
body { margin:0; padding:32px; background:#fbfbfa; color:var(--ink);
       font-family:Georgia, 'Times New Roman', serif; line-height:1.5; }
main { max-width:820px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; font-weight:normal; }
h2 { font-size:15px; margin:32px 0 8px; font-weight:normal;
     border-bottom:1px solid var(--rule); padding-bottom:4px; }
h3 { font-size:13px; margin:16px 0 4px; font-weight:normal; }
p.sub { color:var(--muted); margin:0 0 24px; font-size:12px; }
table { border-collapse:collapse; width:100%; font-size:12px; margin:8px 0 16px; }
th, td { text-align:left; padding:4px 8px; border-bottom:1px solid var(--rule);
         vertical-align:top; }
th { color:var(--muted); font-weight:normal; width:38%; }
td.num { font-variant-numeric:tabular-nums; }
.status { font-size:10px; letter-spacing:0.06em; padding:1px 6px; border:1px solid;
          border-radius:2px; }
.RAN { color:var(--accent); border-color:var(--accent); }
.SUPPRESSED, .NOT_REQUESTED, .FAILED { color:var(--warn); border-color:var(--warn); }
.reason { color:var(--warn); font-size:12px; margin:4px 0 12px; }
.warn { color:var(--warn); font-size:11px; margin:2px 0; }
figure { margin:12px 0; }
svg { max-width:100%; height:auto; border:1px solid var(--rule); }
footer { margin-top:40px; color:var(--muted); font-size:11px;
         border-top:1px solid var(--rule); padding-top:8px; }
"""


def _cell(value: Any) -> str:
    if value is None:
        return "<span class='status FAILED'>undefined</span>"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.6g}"
    if isinstance(value, list | tuple):
        return escape(", ".join(str(item) for item in value))
    if isinstance(value, Mapping):
        return escape("; ".join(f"{k}={v}" for k, v in sorted(value.items())))
    return escape(value)


def _table(rows: Mapping[str, Any]) -> str:
    body = "".join(
        f"<tr><th>{escape(key)}</th><td class='num'>{_cell(value)}</td></tr>"
        for key, value in sorted(rows.items())
    )
    return f"<table>{body}</table>"


def _panel_section(entry: Mapping[str, Any]) -> str:
    status = str(entry["status"])
    parts = [
        f"<h3>{escape(entry['name'])} "
        f"<span class='status {escape(status)}'>{escape(status)}</span></h3>"
    ]
    if status == str(EvidenceStatus.RAN):
        payload = entry["payload"] or {}
        parts.append(_table(payload))
    else:
        detail = ""
        if entry.get("observed") is not None or entry.get("threshold") is not None:
            detail = (
                f" Observed {_cell(entry.get('observed'))}, "
                f"threshold {_cell(entry.get('threshold'))}."
            )
        parts.append(f"<p class='reason'>{escape(entry['reason'])}.{detail}</p>")
    for warning in entry.get("warnings", ()):
        parts.append(f"<p class='warn'>{escape(warning)}</p>")
    return "".join(parts)


def render_html(
    report: ValidationReport,
    *,
    charts: Sequence[str] = (),
    title: str = "Quantify validation report",
) -> str:
    """Render a report as a single self contained HTML document.

    Parameters
    ----------
    report : ValidationReport
    charts : sequence of str, optional
        Complete ``<svg>`` elements, already rendered, inserted in order. They
        are passed in rather than built here so that this module has no opinion
        about which charts a run produced, and so a run with no simulation
        produces a report with no empty axes.
    title : str, optional

    Returns
    -------
    str
        A complete document. Deterministic given a deterministic report and
        deterministic charts.
    """
    payload = report_to_dict(report)
    provenance = payload["provenance"]
    sections = "".join(_panel_section(entry) for entry in payload["panel"])
    figures = "".join(f"<figure>{chart}</figure>" for chart in charts)
    run_warnings = "".join(f"<p class='warn'>{escape(w)}</p>" for w in payload["warnings"])
    absent = [e for e in payload["panel"] if e["status"] != str(EvidenceStatus.RAN)]
    absent_note = (
        f"<p class='reason'>{len(absent)} of {len(payload['panel'])} sections did not run. "
        "An absent test is not a passed test.</p>"
        if absent
        else ""
    )
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body><main>"
        f"<h1>{escape(title)}</h1>"
        f"<p class='sub'>{escape(provenance['input_name'])} &middot; "
        f"sha256 {escape(provenance['input_sha256'][:16])}&hellip; &middot; "
        f"seed {provenance['seed']} &middot; qvalid {escape(provenance['package_version'])} "
        f"&middot; {escape(provenance['executed_at'])}</p>"
        f"{run_warnings}"
        "<h2>Grid</h2>"
        f"{_table(payload['grid'])}"
        "<h2>Declared parameters</h2>"
        f"{_table(payload['parameters'])}"
        "<h2>Evidence panel</h2>"
        f"{absent_note}"
        f"{sections}"
        f"{'<h2>Charts</h2>' + figures if figures else ''}"
        "<h2>Provenance</h2>"
        f"{_table(provenance)}"
        "<footer>No aggregate grade is reported. Collapsing heterogeneous evidence into "
        "one letter is the defect this tool exists to correct; see 02 section 7."
        "</footer></main></body></html>"
    )


def write_html(report: ValidationReport, path: str | Path, *, charts: Sequence[str] = ()) -> Path:
    """Write the HTML rendering to disk and return the path."""
    destination = Path(path)
    destination.write_text(render_html(report, charts=charts), encoding="utf-8")
    return destination
