"""End to end example of the v0.6 pipeline: CSV in, report out.

Run from the repository root::

    python examples/validate_full.py

Equivalent to::

    qvalid validate tests/fixtures/trades_long.csv \
        --config tests/fixtures/run_config_full.yaml --out report.html --also-json

The script imports the library and contains no calculation of its own, per the
prohibition in ``04`` on examples holding logic.
"""

from __future__ import annotations

from pathlib import Path

from qvalid.pipeline import run_validation
from qvalid.report.html import write_html
from qvalid.report.json import write_json
from qvalid.report.latex import write_latex

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
OUTPUT = ROOT / "examples" / "output"


def main() -> None:
    """Validate the sample log and write all three formats."""
    OUTPUT.mkdir(exist_ok=True)
    run = run_validation(FIXTURES / "trades_long.csv", FIXTURES / "run_config_full.yaml")
    report = run.report

    write_html(report, OUTPUT / "report.html", charts=run.charts)
    write_json(report, OUTPUT / "report.json")
    write_latex(report, OUTPUT / "report.tex")

    print(f"{len(report.sections_run)} sections ran: {', '.join(report.sections_run)}")
    for name, reason in report.sections_absent.items():
        print(f"  absent: {name} -> {reason}")
    print()
    metrics = report.entry("calendar_metrics").payload
    print(
        f"Sharpe {metrics['sharpe_sqrt_q']:.3f} "
        f"[{metrics['sharpe_ci_low']:.3f}, {metrics['sharpe_ci_high']:.3f}]"
    )
    drawdown = report.entry("drawdown_distribution").payload
    print(
        f"observed max drawdown {drawdown['observed']:.4f} sits at quantile "
        f"{drawdown['observed_quantile']:.3f} of the simulated distribution"
    )
    regimes = report.entry("regimes").payload
    print(
        f"regime equality of means p = {regimes['equality_of_means_p']:.2e} "
        f"over {regimes['n_states']} states"
    )
    print(f"\nwrote {OUTPUT}/report.{{html,json,tex}}")


if __name__ == "__main__":
    main()
