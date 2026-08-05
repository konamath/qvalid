"""LaTeX rendering, for academic use and for the portfolio.

Emits a fragment rather than a full document by default. A fragment drops into
an existing paper without fighting its preamble; a full document is available
for a standalone build. Both render the same dictionary as the JSON and HTML
outputs, so the three cannot disagree.

No charts. The SVG of :mod:`qvalid.report.svg` would need conversion, and shelling
out to a converter would put a non deterministic external tool inside a pipeline
whose defining property is reproducibility. A LaTeX reader plots from the JSON
with whatever package the surrounding document already uses.

Escaping is the only subtle part. The ten characters LaTeX treats specially are
replaced explicitly, in an order that cannot double escape the backslash, since
a calendar identifier or a warning message can carry any of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from qvalid.report.json import report_to_dict
from qvalid.report.model import EvidenceStatus, ValidationReport

__all__ = ["render_latex", "write_latex"]

_ESCAPES = (
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
)

_PREAMBLE = (
    "\\documentclass[11pt]{article}\n"
    "\\usepackage[margin=2.5cm]{geometry}\n"
    "\\usepackage{booktabs}\n"
    "\\usepackage[T1]{fontenc}\n"
    "\\begin{document}\n"
)


def _escape(text: Any) -> str:
    """Escape LaTeX special characters.

    The backslash is replaced first and its replacement contains braces, which
    would themselves be escaped by the later rules if the order were reversed.
    Doing it in one pass over the original string avoids that.
    """
    out: list[str] = []
    lookup = dict(_ESCAPES)
    for character in str(text):
        out.append(lookup.get(character, character))
    return "".join(out)


def _value(value: Any) -> str:
    if value is None:
        return r"\emph{undefined}"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.6g}"
    if isinstance(value, list | tuple):
        return _escape(", ".join(str(item) for item in value))
    if isinstance(value, Mapping):
        return _escape("; ".join(f"{k}={v}" for k, v in sorted(value.items())))
    return _escape(value)


def _table(caption: str, rows: Mapping[str, Any]) -> str:
    if not rows:
        return ""
    body = "\n".join(f"{_escape(key)} & {_value(item)} \\\\" for key, item in sorted(rows.items()))
    return (
        "\\begin{table}[h]\\centering\n"
        f"\\caption{{{_escape(caption)}}}\n"
        "\\begin{tabular}{ll}\\toprule\n"
        "Field & Value \\\\\\midrule\n"
        f"{body}\n"
        "\\bottomrule\\end{tabular}\\end{table}\n"
    )


def render_latex(report: ValidationReport, *, standalone: bool = False) -> str:
    """Render a report as LaTeX.

    Parameters
    ----------
    report : ValidationReport
    standalone : bool, optional
        ``True`` wraps the fragment in a minimal document with a preamble.

    Returns
    -------
    str
        Deterministic given a deterministic report.
    """
    payload = report_to_dict(report)
    provenance = payload["provenance"]
    parts = [
        "\\section*{Quantify validation report}\n",
        f"\\noindent\\texttt{{{_escape(provenance['input_name'])}}}, "
        f"sha256 \\texttt{{{_escape(provenance['input_sha256'][:16])}}}, "
        f"seed {provenance['seed']}, qvalid {_escape(provenance['package_version'])}, "
        f"{_escape(provenance['executed_at'])}.\n\n",
        _table("Grid", payload["grid"]),
        _table("Declared parameters", payload["parameters"]),
        "\\subsection*{Evidence panel}\n",
    ]
    absent = [e for e in payload["panel"] if e["status"] != str(EvidenceStatus.RAN)]
    if absent:
        parts.append(
            f"\\noindent {len(absent)} of {len(payload['panel'])} sections did not run. "
            "An absent test is not a passed test.\n\n"
        )
    for entry in payload["panel"]:
        status = str(entry["status"])
        parts.append(f"\\subsubsection*{{{_escape(entry['name'])} ({_escape(status)})}}\n")
        if status == str(EvidenceStatus.RAN):
            parts.append(_table(entry["name"], entry["payload"] or {}))
        else:
            parts.append(f"\\noindent {_escape(entry['reason'])}.\n\n")
        for warning in entry.get("warnings", ()):
            parts.append(f"\\noindent\\emph{{{_escape(warning)}}}\n\n")
    parts.append(_table("Provenance", provenance))
    parts.append(
        "\\noindent No aggregate grade is reported. Collapsing heterogeneous evidence "
        "into one letter is the defect this tool exists to correct.\n"
    )
    body = "".join(parts)
    return _PREAMBLE + body + "\\end{document}\n" if standalone else body


def write_latex(report: ValidationReport, path: str | Path, *, standalone: bool = False) -> Path:
    """Write the LaTeX rendering to disk and return the path."""
    destination = Path(path)
    destination.write_text(render_latex(report, standalone=standalone), encoding="utf-8")
    return destination
