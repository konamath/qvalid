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
    typer.echo("pnl_source: COLUMN")
    typer.echo("# The four below are GUESSES, not readings. The header cannot show any of them,")
    typer.echo("# and all four change every number in the report. `qvalid probe` checks two.")
    typer.echo(
        "fee_convention: MAGNITUDE   # DECIDE: MAGNITUDE if costs are positive, "
        "NEGATED if they arrive with a minus sign"
    )
    typer.echo(
        "pnl_convention: NET         # DECIDE: NET if the column is after costs, "
        "GROSS if before. See D017"
    )
    typer.echo(
        'timestamp_format: "%Y-%m-%d %H:%M:%S"   # DECIDE: must match the export exactly, '
        "day first is %d.%m.%Y"
    )
    typer.echo("timezone: America/New_York  # DECIDE: the zone the export's clock is in")
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
def probe(
    log: Path = typer.Argument(..., help="CSV trade log."),
    mapping: Path = typer.Option(..., "--mapping", "-m", help="Column mapping, YAML."),
) -> None:
    """Recover the contract multiplier from the log and draft a symbology map. See D061.

    Unlike ``inspect``, this reads your numbers, and it says so: it inverts the
    P&L identity of ``01`` per trade to recover the multiplier each symbol
    implies. The value is printed beside an empty slot, never into it. D007
    keeps the multiplier a declared input, and a number taken from the same
    file it will later validate is not independent evidence of anything. Its
    use is to disagree with what you declare when what you declare is wrong.
    """
    from qvalid.adapters.probe import Detectability, probe_trade_log, read_declarations
    from qvalid.adapters.tradelog import load_mapping

    for label, target in (("trade log", log), ("mapping", mapping)):
        if not target.is_file():
            typer.echo(f"no {label} at {target}", err=True)
            raise typer.Exit(code=2)
    try:
        declared = load_mapping(mapping)
        seen = read_declarations(log, declared)
        found = probe_trade_log(log, declared)
    except QvalError as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"# draft symbology for {log.name}. The multipliers are NOT filled in.")
    typer.echo("# Each `implied` is what your own file's P&L arithmetic works out to.")
    typer.echo("# Put your contract's real multiplier in the empty slot and compare. See D007.")
    typer.echo("symbols:")
    for entry in found:
        note = (
            f"implied {entry.implied:.6g}, from {entry.n_usable} trades"
            if entry.is_readable
            else (
                f"NOT READABLE: the P&L column is rounded to {entry.pnl_quantum:g}, "
                f"against a typical trade of {entry.typical_pnl:g}"
            )
        )
        typer.echo(f"  {entry.symbol}:")
        typer.echo(f"    multiplier:   # {note}")
        typer.echo("    tick_size:    # smallest price increment; the file cannot show this")
        typer.echo("    venue:        # exchange or broker")
        typer.echo("    currency:")
        typer.echo("    calendar: WEEKDAYS_UTC")
        typer.echo(f"    contract_root: {entry.symbol}")
        typer.echo("    source_ids:")
        typer.echo(f"      generic: {entry.symbol}")

    typer.echo("")
    typer.echo("# P&L convention, which the mapping must declare and the identity cannot check:")
    for entry in found:
        if entry.detectability is Detectability.DECISIVE:
            typer.echo(
                f"#   {entry.symbol}: consistent with pnl_convention: {entry.convention} "
                f"(spread {min(entry.spread_net, entry.spread_gross):.1e} against "
                f"{max(entry.spread_net, entry.spread_gross):.1e} for the other)"
            )
        elif entry.detectability is Detectability.NO_COST:
            typer.echo(f"#   {entry.symbol}: every fee is zero, so NET and GROSS coincide here.")
        else:
            typer.echo(
                f"#   {entry.symbol}: costs are smaller than the rounding of the P&L column, "
                "so the file cannot say. You must know. See D017."
            )

    typer.echo("")
    typer.echo("# What your mapping declares, against what the file shows:")
    if seen.fee_convention_implied is None:
        typer.echo(f"#   fees are {seen.fee_sign}, which implies no convention either way.")
    else:
        agrees = seen.fee_convention_implied == declared.fee_convention.value
        typer.echo(
            f"#   fees are {seen.fee_sign} in the file, implying "
            f"fee_convention: {seen.fee_convention_implied}. "
            + (
                "Agrees with your mapping."
                if agrees
                else f"YOUR MAPPING SAYS {declared.fee_convention.value}, WHICH DISAGREES."
            )
        )
    typer.echo(f"#   first entry stamp reads {seen.sample_entry_ts!r}")
    typer.echo(f"#   first exit stamp reads  {seen.sample_exit_ts!r}")
    if seen.timestamp_format_parses is False:
        typer.echo(
            f"#   YOUR timestamp_format {declared.timestamp_format!r} DOES NOT PARSE THAT. "
            "A day first export needs %d.%m.%Y or %d/%m/%Y."
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
