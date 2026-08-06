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
    from qvalid.drafts import mapping_draft

    if not log.is_file():
        typer.echo(f"no trade log at {log}", err=True)
        raise typer.Exit(code=2)
    with log.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    if not header:
        typer.echo(f"{log} has no header row", err=True)
        raise typer.Exit(code=2)

    typer.echo(mapping_draft(header, source_name=log.name))
    found = suggest_columns(header)
    if not found.is_complete:
        typer.echo("", err=True)
        typer.echo(
            f"{len(found.missing) + len(found.ambiguous)} field(s) need a decision "
            "before this mapping can be used.",
            err=True,
        )


@app.command()
def trials(
    logs: list[Path] = typer.Argument(..., help="One trade log per configuration tested."),
    config: Path = typer.Option(..., "--config", "-c", help="Run configuration, YAML."),
    out: Path = typer.Option(..., "--out", "-o", help="Where to write the matrix."),
) -> None:
    """Build the matrix of every configuration tested, from the logs. See D072.

    The deflation of ``02`` section 3 needs the dispersion across trial Sharpe
    ratios, so a declared count is not enough, and D004 refuses to invent the
    rest. Anyone who swept twenty parameter values has twenty logs; this turns
    them into the artefact the deflation wants.

    The first log is the reference. Its grid is selected by the ladder and
    forced on the others, because D024 makes one grid for every configuration a
    structural precondition rather than something checked afterwards.
    """
    from qvalid.trials import build_trials

    missing = [str(path) for path in [*logs, config] if not path.is_file()]
    if missing:
        typer.echo(f"not found: {', '.join(missing)}", err=True)
        raise typer.Exit(code=2)
    try:
        built = build_trials(logs, config)
    except QvalError as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    out.write_text(built.to_csv(), encoding="utf-8")
    typer.echo(f"wrote {out}")
    typer.echo(
        f"{len(built.names)} configurations on a {built.period.value} grid of "
        f"{built.n_periods} periods, taken from {built.names[0]}"
    )
    if built.worst_trim:
        losers = {name: total for name, (a, b) in built.trimmed.items() if (total := a + b)}
        typer.echo(
            f"trimmed to the window all {len(built.names)} share: "
            f"{len(losers)} configuration(s) lost periods, at most {built.worst_trim}. "
            "A period outside a configuration's own span is an absence, not a zero return."
        )


@app.command()
def fetch(
    symbol: str = typer.Argument(..., help="Series identifier at the source, e.g. SP500."),
    start: str = typer.Option(..., "--start", help="First observation, ISO 8601 date."),
    end: str = typer.Option(..., "--end", help="Last observation, ISO 8601 date."),
    cache_dir: Path = typer.Option(..., "--cache", help="Where the immutable cache lives."),
    source: str = typer.Option("fred", "--source", help="'fred', or 'file' to read a local CSV."),
    from_file: Path | None = typer.Option(
        None, "--file", help="With --source file: the CSV to take the slice from."
    ),
    as_returns: bool = typer.Option(
        False, "--as-returns", help="Convert a level series to simple returns."
    ),
    out: Path | None = typer.Option(None, "--out", help="Also write the reference CSV here."),
) -> None:
    """Bring a slice of external data into the cache. See D074.

    The cache, the manifest, the hash check and the FRED adapter have existed
    since v0.7 and nothing outside their own modules called them. This is the
    command they were missing.

    Every download passes through the cache, so a slice already present is not
    fetched again, and the manifest records **every** request including the
    ones that hit, because omitting the hits would make the log say a slice was
    fetched once when it was used forty times. See D033.

    The key never appears here. ``QVALID_FRED_API_KEY`` is read from the
    environment and the adapter refuses to build without it, so a run that
    cannot work fails before it starts working. See ``03``.
    """
    from qvalid.adapters.cache import CacheKey, LocalCache
    from qvalid.adapters.market import (
        FileFetcher,
        FredFetcher,
        load_series,
        parse_fred_csv,
        parse_two_column_csv,
    )

    if source not in ("fred", "file"):
        typer.echo(f"unknown source {source!r}; use 'fred' or 'file'", err=True)
        raise typer.Exit(code=2)
    if source == "file":
        # Three different problems used to share one message, and the one that
        # actually happens is a path that does not exist. Saying which is the
        # difference between fixing it and guessing at it.
        if from_file is None:
            typer.echo("--source file needs --file pointing at a CSV", err=True)
            raise typer.Exit(code=2)
        if not from_file.exists():
            typer.echo(f"no file at {from_file}", err=True)
            raise typer.Exit(code=2)
        if not from_file.is_file():
            typer.echo(f"{from_file} is a directory, not a CSV", err=True)
            raise typer.Exit(code=2)
    try:
        cache = LocalCache(cache_dir)
        key = CacheKey(source=source, symbol=symbol, start=start, end=end)
        before = cache.downloads()
        # A local file goes through the cache like anything else, so the
        # manifest records where a number came from whether or not it crossed a
        # network. Provenance is the point, not the socket.
        fetcher = FredFetcher() if source == "fred" else FileFetcher(str(from_file))
        parser = parse_fred_csv if source == "fred" else parse_two_column_csv
        series = load_series(cache, key, fetcher, parser=parser)
    except QvalError as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    downloads = cache.downloads()
    hit = downloads == before
    typer.echo(
        f"{key.describe()}: {series.n_observations} observations, "
        f"{'already cached' if hit else 'downloaded'}"
    )
    if series.n_missing:
        typer.echo(f"{series.n_missing} observation(s) the source reported as missing")
    typer.echo(
        f"manifest {cache.manifest_path}, {downloads} download(s) and "
        f"{cache.total_cost():.2f} of estimated cost so far"
    )

    if out is None:
        return
    try:
        written = series.to_returns() if as_returns else series
        out.write_text(written.to_reference_csv(), encoding="utf-8")
    except QvalError as exc:
        typer.echo(f"{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"wrote {out}, {written.n_observations} rows")
    typer.echo(
        "Alignment with a run's grid is by exact timestamp and is checked there, per D032. "
        "A source whose calendar differs from the run's is refused by name rather than "
        "reindexed here."
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
    from qvalid.adapters.probe import probe_trade_log, read_declarations
    from qvalid.adapters.tradelog import load_mapping
    from qvalid.drafts import evidence_lines, symbology_draft

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

    typer.echo(symbology_draft(found, source_name=log.name))
    typer.echo("")
    for line in evidence_lines(seen, found, declared_fee=declared.fee_convention.value):
        typer.echo(f"# {line}")


@app.command()
def mcp(
    cache_dir: Path = typer.Option(..., "--cache", help="The cache to serve, read only."),
) -> None:
    """Serve the local cache to an agent over MCP, on stdin and stdout. See D075.

    This is the integration QuantPad sells: a coding agent that can query the
    same market data your research uses. Over your own cache, with your own key,
    on your own machine.

    Read only. Writing is ``qvalid fetch``, which records a manifest line for
    every request including the ones that hit, and a tool that wrote would put
    data in the cache with no such line. A manifest with a hole reads as
    complete, which D033 calls worse than no manifest.

    Point a client at it with a command like::

        qvalid mcp --cache ~/.qvalid/cache
    """
    from qvalid.mcp.server import serve_stdio

    if not cache_dir.is_dir():
        typer.echo(f"no cache at {cache_dir}; create one with `qvalid fetch`", err=True)
        raise typer.Exit(code=2)
    serve_stdio(cache_dir)


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
