"""Command line entry point.

Deliberately thin. Every decision lives in :mod:`qvalid.pipeline` or in the YAML
configuration, so this module parses arguments, calls the pipeline, writes
files, and translates a typed error into an exit code. Anything else here would
be logic that the test suite can only reach through a subprocess.

Usage follows ``01``::

    qvalid validate log.csv --config cfg.yaml --out report.html
"""

from __future__ import annotations

from pathlib import Path

import typer

from qvalid import __version__
from qvalid.adapters.tradelog import REQUIRED_FIELDS
from qvalid.exceptions import QvalError
from qvalid.pipeline import run_validation
from qvalid.report.html import write_html
from qvalid.report.json import write_json
from qvalid.report.latex import write_latex

__all__ = ["app", "main"]

app = typer.Typer(
    add_completion=False,
    help="Quantify: statistical validation of trading strategies from a trade log.",
    no_args_is_help=True,
)

_SUFFIX_WRITERS = {
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".tex": "latex",
}


@app.command()
def validate(
    log: Path = typer.Argument(..., help="CSV trade log."),
    config: Path = typer.Option(..., "--config", "-c", help="Run configuration, YAML."),
    out: Path = typer.Option(..., "--out", "-o", help="Output file; format from the suffix."),
    also_json: bool = typer.Option(
        False, "--also-json", help="Write the JSON serialisation next to the output."
    ),
) -> None:
    """Validate a trade log and write a report.

    The output format comes from the suffix of ``--out``: ``.html`` for the self
    contained report, ``.json`` for the reference serialisation, ``.tex`` for
    the LaTeX fragment. An unknown suffix is refused rather than guessed,
    because writing HTML into a file named ``.pdf`` helps nobody.
    """
    suffix = out.suffix.lower()
    if suffix not in _SUFFIX_WRITERS:
        typer.echo(
            f"unknown output suffix {suffix!r}; use one of {sorted(set(_SUFFIX_WRITERS))}",
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        run = run_validation(log, config)
    except QvalError as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    kind = _SUFFIX_WRITERS[suffix]
    if kind == "html":
        write_html(run.report, out, charts=run.charts)
    elif kind == "json":
        write_json(run.report, out)
    else:
        write_latex(run.report, out)
    if also_json and kind != "json":
        write_json(run.report, out.with_suffix(".json"))

    absent = run.report.sections_absent
    typer.echo(f"wrote {out}")
    typer.echo(
        f"{len(run.report.sections_run)} sections ran, {len(absent)} did not: {sorted(absent)}"
    )


@app.command()
def inspect(
    log: Path = typer.Argument(..., help="CSV trade log to read the header of."),
) -> None:
    """Read a log's header and print a column mapping to start from. See D060.

    A draft, not a decision. D016 makes the mapping versioned provenance, and a
    file written by a guesser would be provenance nobody chose, so this prints
    and the person saves. What it cannot resolve it says so about, rather than
    picking: a mapping that parses and means something other than what the
    person has is the failure this project exists to remove.
    """
    import csv

    from qvalid.adapters.suggest import suggest_columns

    if not log.is_file():
        typer.echo(f"no trade log at {log}", err=True)
        raise typer.Exit(code=2)
    with log.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    if not header:
        typer.echo(f"{log} has no header row", err=True)
        raise typer.Exit(code=2)

    found = suggest_columns(header)
    typer.echo(f"# draft mapping for {log.name}, from its header. Read it before saving.")
    typer.echo("# Every line is a guess about which column means what. See D016 and D060.")
    typer.echo("source: generic")
    typer.echo("fee_convention: MAGNITUDE   # positive cost. SIGNED if fees arrive negative")
    typer.echo("pnl_convention: NET         # GROSS if the P&L column excludes costs. See D017")
    typer.echo("pnl_source: COLUMN")
    typer.echo('timestamp_format: "%Y-%m-%d %H:%M:%S"   # must match the export exactly')
    typer.echo("timezone: America/New_York  # required when the stamps carry no offset")
    typer.echo("columns:")
    for name in (*REQUIRED_FIELDS, "pnl"):
        if name in found.columns:
            typer.echo(f"  {name}: {found.columns[name]}")
        elif name in found.ambiguous:
            options = found.ambiguous[name]
            reason = (
                f"matched {len(options)} columns, pick one: {', '.join(options)}"
                if len(options) > 1
                else f"another field also matched {options[0]}; decide which gets it"
            )
            typer.echo(f"  {name}:   # UNRESOLVED, {reason}")
        else:
            typer.echo(f"  {name}:   # NOT FOUND in the header")
    typer.echo(f"tag_columns: [{', '.join(found.unused)}]   # columns nothing claimed")

    if not found.is_complete:
        typer.echo("", err=True)
        typer.echo(
            f"{len(found.missing) + len(found.ambiguous)} field(s) need a decision "
            "before this mapping can be used.",
            err=True,
        )


@app.command()
def ui(
    port: int = typer.Option(8765, help="Port on the loopback interface."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open a browser."),
) -> None:
    """Serve the interface on localhost. See D057.

    The interface performs no calculation: it collects the two paths, calls the
    same :func:`~qvalid.pipeline.run_validation` this command calls, and renders
    the report the report layer already knows how to produce. ``05`` makes that
    a permanent constraint, because logic here is debt that would make the
    command line and the interface disagree.
    """
    from qvalid.ui.server import serve

    serve(port, open_browser=open_browser)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
