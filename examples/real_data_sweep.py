"""End to end on real market data, with a real parameter search. See D054.

Everything the tool had seen until now was synthetic: return series drawn from
distributions chosen by whoever wrote the fixture. This runs it on ten years of
S&P 500 daily closes, sweeps a moving average crossover over twenty window
lengths, keeps every configuration including the losers, and pushes the winner
through the importer as a trade log like any other.

What that buys is not realism for its own sake. The autocorrelation, the fat
tails and the regime structure are the market's rather than a modeller's, and
so is the **selection effect**: the winning window is chosen after seeing all
twenty, which is exactly the thing the deflated Sharpe exists to undo.

Get the data first, one command, no key and no account::

    curl -o data/sp500.csv "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"

Then::

    python examples/real_data_sweep.py

Declared assumptions, because they are assumptions and not measurements:

* The index is traded as a one to one instrument with a tick of 0.01. Nobody
  trades the index itself; this stands in for a future or an ETF and the
  multiplier is stated rather than defaulted, per D007.
* Costs are one basis point of notional per side. Real costs depend on the
  broker and the size, and a reader who disagrees should change the constant
  and rerun rather than trust this one.
* Long or flat, never short. A rule that can be short doubles the search space
  and this is a demonstration of the machinery, not a strategy proposal.

The trial matrix is written on the **grid the run will use**, not on the daily
one. A rule that trades seventy seven times in ten years has no daily return
series worth the name, and ``02`` section 1.1 refuses to pretend otherwise: the
ladder picks monthly. Since every configuration has to sit on that same grid,
per section 3, the sweep aggregates to it. Which grid a run picked is in the
``grid_selection`` section of its report. See D054.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PRICES = DATA / "sp500.csv"

WINDOWS = tuple(range(5, 105, 5))
"""Twenty windows. The search whose size the deflation has to be told about."""

COST_PER_SIDE = 1e-4
"""One basis point of notional, each way. Declared, not measured. See the docstring."""

QUANTITY = 10.0
INITIAL_CAPITAL = 100_000.0
"""Matches ``data/run_spx.yaml``. The trials have to be on the run's basis."""
TICK_SIZE = 0.01
SESSION_CLOSE = "21:00:00+00:00"
"""16:00 New York, which is where the venue calendar puts a daily period end."""


def load_prices() -> pd.DataFrame:
    """Read the FRED export and drop the days it marks as missing with a dot."""
    if not PRICES.is_file():
        raise SystemExit(
            f"missing {PRICES.relative_to(ROOT)}. Fetch it with:\n"
            '  curl -o data/sp500.csv "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"'
        )
    frame = pd.read_csv(PRICES)
    frame.columns = ["date", "close"]
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def signal(close: np.ndarray, window: int) -> np.ndarray:
    """Long when yesterday's close was above yesterday's trailing mean.

    Both sides of the comparison are lagged, so the position held through day
    ``t`` is decided with information available at the close of ``t - 1``.
    Comparing today's close against today's mean and earning today's return is
    the commonest look ahead in this kind of rule.
    """
    trailing = pd.Series(close).rolling(window).mean().to_numpy()
    held = np.zeros(close.size, dtype=np.float64)
    held[1:] = (close[:-1] > trailing[:-1]).astype(np.float64)
    return np.nan_to_num(held)


def net_returns(close: np.ndarray, held: np.ndarray) -> np.ndarray:
    """Daily return on the run's basis: position P&L over the initial capital.

    Not the price return of the position. ``02`` section 3 requires every
    configuration to be on the same grid **and the same basis** as the observed
    strategy, and the run divides by a fixed initial capital. Dividing by the
    position's own notional instead would leave the Sharpe right and every
    other number on a different scale.
    """
    pnl = np.zeros(close.size, dtype=np.float64)
    pnl[1:] = held[1:] * QUANTITY * (close[1:] - close[:-1])
    turnover = np.abs(np.diff(held, prepend=0.0))
    cost = turnover * COST_PER_SIDE * QUANTITY * close
    return (pnl - cost) / INITIAL_CAPITAL


def trades_of(frame: pd.DataFrame, held: np.ndarray) -> pd.DataFrame:
    """Turn each contiguous long spell into one closed trade."""
    close = frame["close"].to_numpy()
    changes = np.diff(held, prepend=0.0)
    entries = np.flatnonzero(changes > 0)
    exits = np.flatnonzero(changes < 0)
    if exits.size < entries.size:
        entries = entries[: exits.size]

    rows = []
    for number, (start, stop) in enumerate(zip(entries, exits, strict=True), start=1):
        entry_px, exit_px = float(close[start]), float(close[stop])
        fees = COST_PER_SIDE * QUANTITY * (entry_px + exit_px)
        rows.append(
            {
                "id": f"T{number:05d}",
                "instrument": "SPX",
                "direction": "long",
                "quantity": QUANTITY,
                "opened_at": f"{frame['date'].iloc[start].date()} {SESSION_CLOSE}",
                "closed_at": f"{frame['date'].iloc[stop].date()} {SESSION_CLOSE}",
                "open_price": round(entry_px, 2),
                "close_price": round(exit_px, 2),
                "commission": round(fees, 6),
                "net_pnl": round((exit_px - entry_px) * QUANTITY - fees, 6),
                "setup": "ma_cross",
            }
        )
    return pd.DataFrame(rows)


def monthly_periods(dates: pd.Series) -> pd.DatetimeIndex:
    """Month ends over the span, stamped the way the venue calendar stamps them.

    The last instant of the last day of the month, in UTC. Matching the
    convention matters as much as matching the dates: alignment is by exact
    timestamp, per D032, so a month end at midnight would miss every period by
    a day.
    """
    return pd.date_range(dates.iloc[0], dates.iloc[-1], freq="ME", tz="UTC")


def to_monthly(daily: np.ndarray, dates: pd.Series, periods: pd.DatetimeIndex) -> np.ndarray:
    """Sum daily returns within each month.

    Summing is right and averaging is not: the basis is a fixed initial
    capital, so period returns are additive by construction.
    """
    frame = pd.DataFrame({"value": daily}, index=pd.DatetimeIndex(dates).tz_localize("UTC"))
    grouped = frame.resample("ME").sum()
    return np.asarray(grouped.reindex(periods)["value"].fillna(0.0).to_numpy(), dtype=np.float64)


def main() -> None:
    """Sweep, write the matrix and the winner's trades, and report the search."""
    frame = load_prices()
    close = frame["close"].to_numpy()
    DATA.mkdir(exist_ok=True)

    held = {window: signal(close, window) for window in WINDOWS}
    returns = {window: net_returns(close, held[window]) for window in WINDOWS}
    sharpes = {
        window: float(series.mean() / series.std(ddof=1)) for window, series in returns.items()
    }
    best = max(sharpes, key=lambda key: sharpes[key])

    trades = trades_of(frame, held[best])
    trades.to_csv(DATA / "trades_spx.csv", index=False, lineterminator="\n")

    periods = monthly_periods(frame["date"])
    matrix: dict[str, object] = {
        "period_end": pd.Index([f"{end.date()} 23:59:59.999999999+00:00" for end in periods])
    }
    for window in WINDOWS:
        matrix[f"ma_{window}"] = to_monthly(returns[window], frame["date"], periods)
    pd.DataFrame(matrix).to_csv(DATA / "trials_spx.csv", index=False, lineterminator="\n")

    first, last = frame["date"].iloc[0].date(), frame["date"].iloc[-1].date()
    print(f"{frame.shape[0]} sessions, {first} to {last}")
    print(f"{len(WINDOWS)} configurations swept, best is ma_{best}")
    print(f"  best   per period Sharpe {sharpes[best]:+.4f}")
    print(f"  median per period Sharpe {np.median(list(sharpes.values())):+.4f}")
    print(f"  worst  per period Sharpe {min(sharpes.values()):+.4f}")
    print(f"\n{len(trades)} trades written to data/trades_spx.csv")
    print(f"{len(periods)} monthly rows written to data/trials_spx.csv")


if __name__ == "__main__":
    main()
