"""End to end example: a CSV on disk becomes a judged strategy.

Run from the repository root::

    python examples/validate_csv.py

This script imports the library and contains no calculation of its own, per the
prohibition in ``04`` on notebooks and examples holding logic. It exists to make
the v0.1 criterion of ``05`` verifiable by command.

Every number that changes the result is printed, because ``01`` requires the
report to be reproducible from what it declares: grid, calendar, basis, initial
capital, active fraction, risk free rate, HAC bandwidth and confidence level.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from qvalid.adapters.calendars import weekdays_utc
from qvalid.adapters.symbology import load_symbology
from qvalid.adapters.tradelog import load_mapping, read_trade_log_csv
from qvalid.contracts import Basis, Period
from qvalid.core.gridding import select_grid, trade_returns
from qvalid.core.metrics import period_metrics, trade_metrics

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
INITIAL_CAPITAL = 50_000.0
RISK_FREE_RATE = 0.045


def main() -> None:
    """Import, project, measure and print."""
    symbology = load_symbology(FIXTURES / "symbology.yaml")
    mapping = load_mapping(FIXTURES / "mapping_generic.yaml")
    imported = read_trade_log_csv(FIXTURES / "trades_generic.csv", mapping, symbology)

    exits = np.asarray(imported.log.exit_ns)
    first = datetime.fromtimestamp(int(exits.min()) / 1e9, tz=UTC)
    last = datetime.fromtimestamp(int(exits.max()) / 1e9, tz=UTC)
    calendar = weekdays_utc(first - timedelta(days=7), last + timedelta(days=7))

    selection = select_grid(
        imported.log,
        calendar,
        basis=Basis.FIXED_INITIAL,
        initial_capital=INITIAL_CAPITAL,
        forced_period=Period.DAILY,
    )
    calendar_metrics = period_metrics(selection.returns, risk_free_rate=RISK_FREE_RATE)
    per_trade = trade_metrics(
        trade_returns(imported.log, basis=Basis.FIXED_INITIAL, initial_capital=INITIAL_CAPITAL)
    )

    print(f"imported {imported.n_rows_read} rows in {imported.currency}")
    print(f"calendars {imported.calendars}")
    for warning in imported.warnings:
        print(f"  import warning: {warning}")

    print()
    print(f"grid {calendar_metrics.period} on calendar {calendar_metrics.calendar_id}")
    print(
        f"  periods {calendar_metrics.n_periods}, active fraction "
        f"{calendar_metrics.active_fraction:.4f}, years {calendar_metrics.years:.3f}"
    )
    for candidate in selection.candidates:
        state = "feasible" if candidate.feasible else "; ".join(candidate.rejections)
        print(f"  rung {candidate.period}: {state}")
    for warning in selection.warnings:
        print(f"  grid warning: {warning}")

    print()
    print("per trade, never annualised")
    print(
        f"  n {per_trade.n_trades}, expectancy {per_trade.expectancy:.6f}, "
        f"hit rate {per_trade.hit_rate:.4f}"
    )
    print(f"  profit factor {per_trade.profit_factor}, kurtosis {per_trade.kurtosis:.3f}")

    sharpe = calendar_metrics.sharpe
    print()
    print("calendar anchored")
    print(f"  cumulative return {calendar_metrics.cumulative_return:.4f}")
    print(f"  Sharpe sqrt(q) {sharpe.annualised_sqrt_q}, HAC {sharpe.annualised_hac}")
    print(
        f"  interval at {sharpe.confidence_level:.0%} "
        f"[{sharpe.ci_low}, {sharpe.ci_high}], bandwidth {sharpe.bandwidth}"
    )
    print(
        f"  risk free {sharpe.risk_free_rate_annual} annual, "
        f"{sharpe.risk_free_rate_per_period:.3e} per period"
    )
    for warning in calendar_metrics.warnings:
        print(f"  warning: {warning}")


if __name__ == "__main__":
    main()
