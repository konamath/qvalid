"""The engine's statistics over a market series, in one place two front ends share.

Extracted for the reason ``drafts.py`` was extracted in D063: the alternative is
two copies that drift, and here a drift would mean the command and the agent
reporting different volatilities for the same file.

The layering matters as much as the sharing. ``cli.py`` importing this is a front
end importing a shared module; ``cli.py`` importing :mod:`qvalid.mcp.tools` would
be one front end importing another, which is how a command ends up depending on
a protocol it does not speak.

**Why this module exists at all.** Asked for the annualised volatility and the
maximum drawdown of a cached series, an agent connected to the v2.4 tools read
the array through ``read_series``, found nothing that would compute anything
with it, and wrote the mathematics into a scratch file: ``statistics.stdev``
times the square root of a hard coded 252. The drawdown came out right. The
volatility came out wrong by the ratio between 252 and the rate the series
actually shows, and nothing in the answer said which rate had been used, or on
what basis the equity path had been built, or that the interval around the
Sharpe ratio straddled zero from end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from qvalid.adapters.market import MarketSeries
from qvalid.core.metrics import period_metrics

__all__ = ["describe_period_metrics"]


def _maybe(value: float | None) -> float | None:
    return None if value is None else float(value)


def _iso(stamp_ns: int) -> str:
    return datetime.fromtimestamp(stamp_ns / 1e9, tz=UTC).isoformat()


def describe_period_metrics(series: MarketSeries, *, risk_free_rate: float = 0.0) -> dict[str, Any]:
    """Run :func:`~qvalid.core.metrics.period_metrics` over a level series.

    Parameters
    ----------
    series : MarketSeries
        Levels. Declared as a calendar grid by
        :meth:`~qvalid.adapters.market.MarketSeries.to_period_returns`, which
        derives the rate from the observation stamps and refuses rather than
        guessing when they do not support one.
    risk_free_rate : float, optional
        Simple annual rate. Reported back, because a Sharpe ratio whose risk
        free rate is not stated is not reproducible.

    Returns
    -------
    dict
        Plain JSON types. Carries every field that reproduces the numbers, per
        ``01``: the period, the rate, the identifier saying whether that rate
        was observed or nominal, the basis, the initial level, the Bartlett
        bandwidth, the confidence level and the warnings.

    Raises
    ------
    SchemaError
        Propagated from the declaration. A series spaced in a way that matches
        no grid, or too irregular to annualise, produces no number here at all.

    Notes
    -----
    Nothing is rounded and nothing is phrased. A caller that wants prose writes
    it; the five discoveries recorded in D052, D056, D073 and D074 were all made
    easier by numbers that were still numbers when they reached the reader.
    """
    returns = series.to_period_returns()
    metrics = period_metrics(returns, risk_free_rate=risk_free_rate)
    sharpe = metrics.sharpe
    drawdown = metrics.drawdown
    return {
        "series_id": series.series_id,
        "first_observation": _iso(int(series.timestamp_ns[0])),
        "last_observation": _iso(int(series.timestamp_ns[-1])),
        "n_levels": series.n_observations,
        "n_missing": series.n_missing,
        "grid": {
            "period": metrics.period.value,
            "periods_per_year": metrics.periods_per_year,
            "calendar_id": metrics.calendar_id,
            "n_periods": metrics.n_periods,
            "years": metrics.years,
        },
        "basis": metrics.basis.value,
        "initial_level": metrics.initial_capital,
        "cumulative_return": metrics.cumulative_return,
        "cagr": _maybe(metrics.cagr),
        "volatility_annualised": metrics.volatility_annualised,
        "sortino_annualised": _maybe(metrics.sortino_annualised),
        "kelly_fraction": _maybe(metrics.kelly_fraction),
        "sharpe": {
            "annualised_sqrt_q": _maybe(sharpe.annualised_sqrt_q),
            "annualised_hac": _maybe(sharpe.annualised_hac),
            "standard_error": _maybe(sharpe.standard_error),
            "ci_low": _maybe(sharpe.ci_low),
            "ci_high": _maybe(sharpe.ci_high),
            "confidence_level": sharpe.confidence_level,
            "bandwidth": sharpe.bandwidth,
            "risk_free_rate_annual": sharpe.risk_free_rate_annual,
        },
        "drawdown": None
        if drawdown is None
        else {
            "max_drawdown": drawdown.max_drawdown,
            "max_drawdown_duration": drawdown.max_drawdown_duration,
            "recovered": drawdown.recovered,
            "time_underwater": drawdown.time_underwater,
        },
        "warnings": list(metrics.warnings),
    }
