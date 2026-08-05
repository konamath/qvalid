"""Composition root: the ten steps of ``01`` wired together.

This is the only module allowed to import ``adapters``, ``core`` and ``report``
at the same time. Everything below it obeys the dependency rule of ``01`` and
points inward; putting the wiring anywhere else would force one of the layers to
know about another. ``cli.py`` stays thin on purpose: it parses arguments, calls
:func:`run_validation`, and writes files, so the pipeline stays testable without
a subprocess.

Every section is wrapped so that a typed failure becomes an :class:`Evidence`
entry rather than an aborted run. A report that dies on the regime section
teaches less than one that shows the regime section failing and everything else
holding, and ``02`` section 7 requires the absence to be visible either way.

The configuration is a versioned YAML file, for the reason D016 gives for the
column mapping: every parameter that changes a number has to be recoverable by
someone else, and an argument typed into a shell is not.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qvalid import __version__
from qvalid.adapters.calendars import weekdays_utc
from qvalid.adapters.symbology import load_symbology
from qvalid.adapters.tradelog import load_mapping, read_trade_log_csv
from qvalid.contracts import Basis, FloatArray, IntArray, Period
from qvalid.core.constants import DEFAULT_CONFIDENCE_LEVEL, PNL_RTOL
from qvalid.core.gridding import select_grid, trade_returns
from qvalid.core.metrics import period_metrics, trade_metrics
from qvalid.core.regimes import attribute_by_regime, label_regimes
from qvalid.core.resample import resample_equity_paths
from qvalid.core.risk import (
    drawdown_distribution,
    expected_shortfall,
    first_passage,
    terminal_return,
    value_at_risk,
)
from qvalid.core.verdict import (
    DEFAULT_RANKING_REQUIREMENTS,
    Candidate,
    CptParameters,
    rank,
)
from qvalid.exceptions import QvalError, SchemaError
from qvalid.report.model import Evidence, EvidenceStatus, RunProvenance, ValidationReport
from qvalid.report.svg import bar_chart, histogram, line_chart

__all__ = ["RunConfig", "ValidationRun", "load_config", "run_validation", "sha256_of"]

_CALENDAR_PADDING_DAYS = 7
"""Padding on each side of the log, so the sentinel calendar covers it."""


class RunConfig(BaseModel):
    """Every parameter that changes a number, in one versioned file.

    Attributes
    ----------
    symbology_path, mapping_path : str
        Paths to the two YAML files of D016, relative to the config file.
    initial_capital : float
    basis : Basis
    seed : int
        Mandatory. Every stochastic step takes it explicitly, per ``04``.
    risk_free_rate : float
        Simple annual. Default zero, declared and printed.
    n_paths : int
        Monte Carlo replications.
    forced_period : Period or None
        Pins the grid instead of letting the ladder choose. The three
        feasibility conditions then degrade to warnings, per ``02`` 1.1.
    ruin_barrier : float or None
        Absolute equity level. ``None`` skips the section rather than guessing.
    n_trials : int or None
        Configurations tested. ``None`` means the deflated Sharpe does not run
        and the report says so, per D004.
    reference_path : str or None
        CSV with one column of reference returns per period, for the regime
        grid. ``None`` skips the regime section.
    confidence_level : float
    verdict_requirements : tuple of str
        Sections that must have run before a certainty equivalent is formed.
        Defaults to the strict list of ``core/verdict.py``. Shortening it is a
        **declaration**, not a loophole: the shortened list enters the report
        and the configuration hash, so a reader sees exactly which evidence the
        ranking was allowed to do without. It exists because a single trade log
        cannot supply the trial matrix the deflated Sharpe needs, so the strict
        list makes the verdict unreachable from this pipeline by construction.
        See D039.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbology_path: str
    mapping_path: str
    initial_capital: float = Field(gt=0.0)
    seed: int
    basis: Basis = Basis.FIXED_INITIAL
    risk_free_rate: float = 0.0
    n_paths: int = Field(default=10_000, gt=0)
    forced_period: Period | None = None
    ruin_barrier: float | None = None
    n_trials: int | None = None
    reference_path: str | None = None
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
    verdict_requirements: tuple[str, ...] = DEFAULT_RANKING_REQUIREMENTS


def sha256_of(path: str | Path) -> str:
    """Hash a file's bytes. The hash, not the path, is what identifies the data."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_config(path: str | Path) -> RunConfig:
    """Load and validate the run configuration.

    Raises
    ------
    SchemaError
        Missing file, malformed YAML, or any field failing validation. Callers
        of this layer catch one exception type, per ``04``.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SchemaError(f"run configuration not found at {file_path}")
    try:
        raw: Any = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchemaError(f"run configuration at {file_path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError(
            f"run configuration at {file_path} must be a mapping, got {type(raw).__name__}"
        )
    try:
        return RunConfig.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"run configuration at {file_path} is invalid: {exc}") from exc


def _section(
    name: str, compute: Callable[[], dict[str, Any]], *, warnings: tuple[str, ...] = ()
) -> Evidence:
    """Run one section, turning a typed failure into evidence rather than a crash."""
    try:
        payload = compute()
    except QvalError as exc:
        return Evidence(
            name=name,
            status=EvidenceStatus.FAILED,
            reason=f"{type(exc).__name__}: {exc}",
            observed=getattr(exc, "observed", None),
            threshold=getattr(exc, "threshold", None),
        )
    return Evidence(name=name, status=EvidenceStatus.RAN, payload=payload, warnings=warnings)


def _number(value: Any) -> Any:
    """Coerce NumPy scalars and non finite floats into report friendly values."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


class ValidationRun:
    """Result of a run: the report plus the charts it produced.

    Kept as a small class rather than a tuple so the caller cannot swap the two
    by accident, and so a future output format can be added without changing
    every call site.
    """

    __slots__ = ("charts", "report")

    def __init__(self, report: ValidationReport, charts: tuple[str, ...]) -> None:
        self.report = report
        self.charts = charts


def run_validation(
    log_path: str | Path,
    config_path: str | Path,
    *,
    executed_at: str | None = None,
) -> ValidationRun:
    """Run the ten steps of ``01`` and assemble the report.

    Parameters
    ----------
    log_path : str or pathlib.Path
        CSV trade log.
    config_path : str or pathlib.Path
        Run configuration, see :class:`RunConfig`.
    executed_at : str or None, optional
        ISO 8601 timestamp. Injectable so the byte for byte criterion of ``05``
        can be checked by holding the one field that is allowed to vary.

    Returns
    -------
    ValidationRun

    Notes
    -----
    Order follows ``01``: import, calendar, grid projection, descriptive
    metrics, regimes, resampling, risk, overfitting, and serialisation. The
    verdict of v0.9 slots in before serialisation and reads the same panel.

    Sections that need an input only the user can supply are marked
    ``NOT_REQUESTED`` rather than skipped silently. The deflated Sharpe without
    a declared number of trials is the case D004 names, and the regime section
    without a reference series is the same shape of decision.
    """
    config = load_config(config_path)
    base = Path(config_path).resolve().parent
    symbology = load_symbology(base / config.symbology_path)
    mapping = load_mapping(base / config.mapping_path)
    imported = read_trade_log_csv(log_path, mapping, symbology)

    exits = np.asarray(imported.log.exit_ns)
    first = datetime.fromtimestamp(int(exits.min()) / 1e9, tz=UTC)
    last = datetime.fromtimestamp(int(exits.max()) / 1e9, tz=UTC)
    padding = timedelta(days=_CALENDAR_PADDING_DAYS)
    calendar = weekdays_utc(first - padding, last + padding)

    selection = select_grid(
        imported.log,
        calendar,
        basis=config.basis,
        initial_capital=config.initial_capital,
        forced_period=config.forced_period,
    )
    returns = selection.returns
    calendar_metrics = period_metrics(
        returns,
        risk_free_rate=config.risk_free_rate,
        confidence_level=config.confidence_level,
    )
    per_trade = trade_metrics(
        trade_returns(imported.log, basis=config.basis, initial_capital=config.initial_capital)
    )

    panel: list[Evidence] = []
    charts: list[str] = []

    panel.append(
        Evidence(
            name="trade_metrics",
            status=EvidenceStatus.RAN,
            payload={
                "n_trades": per_trade.n_trades,
                "expectancy": _number(per_trade.expectancy),
                "hit_rate": _number(per_trade.hit_rate),
                "profit_factor": _number(per_trade.profit_factor),
                "win_loss_ratio": _number(per_trade.win_loss_ratio),
                "skewness": _number(per_trade.skewness),
                "kurtosis": _number(per_trade.kurtosis),
            },
            warnings=per_trade.warnings,
        )
    )

    sharpe = calendar_metrics.sharpe
    panel.append(
        Evidence(
            name="calendar_metrics",
            status=EvidenceStatus.RAN,
            payload={
                "cumulative_return": _number(calendar_metrics.cumulative_return),
                "cagr": _number(calendar_metrics.cagr),
                "volatility_annualised": _number(calendar_metrics.volatility_annualised),
                "sharpe_sqrt_q": _number(sharpe.annualised_sqrt_q),
                "sharpe_hac": _number(sharpe.annualised_hac),
                "sharpe_ci_low": _number(sharpe.ci_low),
                "sharpe_ci_high": _number(sharpe.ci_high),
                "sortino_annualised": _number(calendar_metrics.sortino_annualised),
                "kelly_fraction": _number(calendar_metrics.kelly_fraction),
                "max_drawdown": _number(
                    calendar_metrics.drawdown.max_drawdown if calendar_metrics.drawdown else None
                ),
            },
            warnings=calendar_metrics.warnings,
        )
    )

    grid_payload = {
        "chosen": str(selection.period),
        "forced": selection.forced,
        "candidates": [
            f"{c.period}: n={c.n_periods}, active={c.active_fraction:.4f}, "
            f"holding={c.holding_ratio:.4f}, feasible={c.feasible}"
            for c in selection.candidates
        ],
    }
    panel.append(
        Evidence(
            name="grid_selection",
            status=EvidenceStatus.RAN,
            payload=grid_payload,
            warnings=selection.warnings,
        )
    )

    bootstrap = None
    try:
        bootstrap = resample_equity_paths(returns, n_paths=config.n_paths, seed=config.seed)
        panel.append(
            Evidence(
                name="resampling",
                status=EvidenceStatus.RAN,
                payload={
                    "block_length": _number(bootstrap.block_length.block_length),
                    "automatic": bootstrap.block_length.automatic,
                    "capped": bootstrap.block_length.capped,
                    "n_paths": bootstrap.paths.n_paths,
                    "n_steps": bootstrap.paths.n_steps,
                    "method": bootstrap.paths.method,
                },
                warnings=bootstrap.warnings,
            )
        )
    except QvalError as exc:
        panel.append(
            Evidence(
                name="resampling",
                status=EvidenceStatus.FAILED,
                reason=f"{type(exc).__name__}: {exc}",
                observed=getattr(exc, "observed", None),
                threshold=getattr(exc, "threshold", None),
            )
        )

    if bootstrap is None:
        for name in ("risk_tail", "drawdown_distribution", "risk_of_ruin"):
            panel.append(
                Evidence(
                    name=name,
                    status=EvidenceStatus.SUPPRESSED,
                    reason="resampling did not produce paths, so no simulated statistic exists",
                )
            )
    else:
        paths = bootstrap.paths
        panel.append(
            _section(
                "risk_tail",
                lambda: {
                    "value_at_risk": _number(
                        value_at_risk(
                            paths, seed=config.seed, confidence_level=config.confidence_level
                        ).value
                    ),
                    "expected_shortfall": _number(
                        expected_shortfall(
                            paths, seed=config.seed, confidence_level=config.confidence_level
                        ).value
                    ),
                },
            )
        )
        observed_drawdown = (
            calendar_metrics.drawdown.max_drawdown if calendar_metrics.drawdown else None
        )

        def _drawdown() -> dict[str, Any]:
            distribution = drawdown_distribution(
                paths,
                seed=config.seed,
                observed=observed_drawdown,
                confidence_level=config.confidence_level,
            )
            charts.append(
                histogram(
                    np.asarray(
                        [distribution.quantiles[q] for q in sorted(distribution.quantiles)] * 2
                    ),
                    title="Simulated maximum drawdown, quantiles",
                    x_label="fraction of peak",
                    y_label="count",
                    marker=observed_drawdown,
                )
            )
            return {
                "mean": _number(distribution.mean.value),
                "standard_error": _number(distribution.mean.standard_error),
                "observed": _number(distribution.observed),
                "observed_quantile": _number(distribution.observed_quantile),
                "quantiles": {str(k): _number(v) for k, v in distribution.quantiles.items()},
            }

        panel.append(_section("drawdown_distribution", _drawdown))

        if config.ruin_barrier is None:
            panel.append(
                Evidence(
                    name="risk_of_ruin",
                    status=EvidenceStatus.NOT_REQUESTED,
                    reason="no ruin barrier was declared, and a barrier determines the "
                    "answer, so none was assumed",
                )
            )
        else:
            barrier = config.ruin_barrier
            panel.append(
                _section(
                    "risk_of_ruin",
                    lambda: _ruin_payload(paths, barrier, config),
                )
            )

    if config.n_trials is None:
        panel.append(
            Evidence(
                name="deflated_sharpe",
                status=EvidenceStatus.NOT_REQUESTED,
                reason="the number of configurations tested was not declared, so no "
                "correction for search was applied; estimating it would fabricate the "
                "input that determines the result, see D004",
            )
        )
    else:
        panel.append(
            Evidence(
                name="deflated_sharpe",
                status=EvidenceStatus.NOT_REQUESTED,
                reason="a trial count was declared but the matrix of all tested "
                "configurations was not supplied; the deflation needs the dispersion "
                "across trial Sharpe ratios, which a single log cannot provide",
            )
        )

    reference = _load_reference(base, config, np.asarray(returns.period_end_ns))
    if reference is None:
        panel.append(
            Evidence(
                name="regimes",
                status=EvidenceStatus.NOT_REQUESTED,
                reason="no reference market series was supplied, so no regime grid could "
                "be built; the engine never fetches one, see 01",
            )
        )
    else:

        def _regimes() -> dict[str, Any]:
            labels = label_regimes(
                reference,
                np.asarray(returns.period_end_ns),
                reference_id=str(config.reference_path),
            )
            attribution = attribute_by_regime(returns, labels)
            states = sorted(attribution.totals)
            charts.append(
                bar_chart(
                    [str(s) for s in states],
                    [attribution.totals[s] for s in states],
                    title="Return attributed by regime state",
                    x_label="joint state",
                    y_label="sum of period returns",
                )
            )
            equality = attribution.equality_of_means
            return {
                "reference_id": attribution.reference_id,
                "window": labels.window,
                "warmup": labels.warmup,
                "n_states": labels.n_states,
                "concentration": _number(attribution.concentration),
                "undefined_periods": attribution.undefined_periods,
                "equality_of_means_p": _number(equality.p_value) if equality else None,
                "totals": {str(k): _number(v) for k, v in attribution.totals.items()},
            }

        panel.append(_section("regimes", _regimes))

    ran_so_far = tuple(entry.name for entry in panel if entry.status is EvidenceStatus.RAN)
    absent_so_far = {
        entry.name: f"{entry.status}: {entry.reason}"
        for entry in panel
        if entry.status is not EvidenceStatus.RAN
    }
    if bootstrap is None:
        panel.append(
            Evidence(
                name="verdict",
                status=EvidenceStatus.SUPPRESSED,
                reason="no simulated distribution of outcomes exists, so no certainty "
                "equivalent can be formed",
            )
        )
    else:
        cpt = CptParameters()
        ranking = rank(
            [
                Candidate(
                    name="strategy",
                    outcomes=terminal_return(bootstrap.paths),
                    sections_run=ran_so_far,
                    sections_absent=absent_so_far,
                )
            ],
            params=cpt,
            requirements=tuple(config.verdict_requirements),
        )
        if ranking.ranked:
            best = ranking.ranked[0]
            panel.append(
                Evidence(
                    name="verdict",
                    status=EvidenceStatus.RAN,
                    payload={
                        "certainty_equivalent": _number(best.certainty_equivalent),
                        "cpt_value": _number(best.cpt_value),
                        "parameters": cpt.model_dump(),
                        "requirements": list(ranking.requirements),
                        "requirements_are_default": tuple(ranking.requirements)
                        == DEFAULT_RANKING_REQUIREMENTS,
                    },
                    warnings=(
                        "the certainty equivalent ranks a distribution under declared "
                        "preferences; it is never a grade and never replaces the panel above",
                    ),
                )
            )
        else:
            blocked = ranking.unrankable[0]
            panel.append(
                Evidence(
                    name="verdict",
                    status=EvidenceStatus.SUPPRESSED,
                    reason=blocked.reason,
                    observed=list(blocked.blocking_sections),
                    threshold=list(ranking.requirements),
                )
            )

    charts.insert(
        0,
        line_chart(
            np.asarray(returns.values).cumsum() * config.initial_capital + config.initial_capital,
            title="Observed equity, additive on initial capital",
            x_label="period",
            y_label="account currency",
        ),
    )

    provenance = RunProvenance(
        package_version=__version__,
        input_name=log_path.name,
        input_sha256=sha256_of(log_path),
        config_sha256=sha256_of(config_path),
        seed=config.seed,
        n_replications=config.n_paths,
        executed_at=executed_at
        or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    report = ValidationReport(
        provenance=provenance,
        grid={
            "period": str(returns.period),
            "periods_per_year": returns.periods_per_year,
            "calendar_id": returns.calendar_id,
            "basis": str(returns.basis),
            "initial_capital": returns.initial_capital,
            "active_fraction": returns.active_fraction,
            "n_periods": returns.n_periods,
            "years": returns.years,
        },
        parameters={
            "risk_free_rate": config.risk_free_rate,
            "risk_free_rate_per_period": _number(sharpe.risk_free_rate_per_period),
            "hac_bandwidth": sharpe.bandwidth,
            "hac_lag_selection": sharpe.lag_selection_parameter,
            "confidence_level": config.confidence_level,
            "pnl_rtol": PNL_RTOL,
            "pnl_atol_rule": "tick_size * multiplier * qty, one tick per round turn",
            "currency": imported.currency,
            "ruin_barrier": config.ruin_barrier,
            "n_trials": config.n_trials,
            "verdict_requirements": list(config.verdict_requirements),
        },
        panel=tuple(panel),
        warnings=imported.warnings,
    )
    return ValidationRun(report, tuple(charts))


def _ruin_payload(paths: Any, barrier: float, config: RunConfig) -> dict[str, Any]:
    passage = first_passage(
        paths, barrier=barrier, seed=config.seed, confidence_level=config.confidence_level
    )
    return {
        "barrier": barrier,
        "probability": _number(passage.probability.value),
        "standard_error": _number(passage.probability.standard_error),
        "ci_low": _number(passage.probability.ci_low),
        "ci_high": _number(passage.probability.ci_high),
        "horizon_steps": passage.horizon_steps,
        "never_hit_fraction": _number(passage.never_hit_fraction),
        "time_quantiles": {str(k): _number(v) for k, v in passage.quantiles.items()},
    }


def _load_reference(base: Path, config: RunConfig, period_end_ns: IntArray) -> FloatArray | None:
    """Read the reference return series and align it to the grid **by timestamp**.

    Aligning by position would be the obvious implementation and would be
    wrong. A reference file that starts one session earlier than the grid, or
    that includes a holiday the venue calendar excludes, would shift every
    label by one period. The regime module already refuses a misalignment it
    can see, by comparing closing instants, but a positional read here would
    hand it a series that looks aligned and is not. That is the silent failure
    mode this project exists to remove, so the file has to carry timestamps.

    Expected format: two columns with a header, the first an ISO 8601
    timezone aware timestamp of the period close, the second the return.
    """
    if config.reference_path is None:
        return None
    path = base / config.reference_path
    if not path.is_file():
        raise SchemaError(f"reference series not found at {path}")
    frame = pd.read_csv(path)
    if frame.shape[1] < 2:
        raise SchemaError(
            f"the reference series at {path} needs a timestamp column and a return column; "
            "aligning by position would silently shift every regime label"
        )
    stamps = pd.to_datetime(frame.iloc[:, 0], utc=False)
    if stamps.dt.tz is None:
        raise SchemaError(
            f"the reference timestamps at {path} are naive; 01 forbids naive timestamps "
            "at every boundary"
        )
    lookup = dict(
        zip(
            stamps.dt.tz_convert("UTC").astype("int64").to_numpy(),
            frame.iloc[:, 1].to_numpy(dtype=np.float64),
            strict=True,
        )
    )
    wanted = np.asarray(period_end_ns, dtype=np.int64)
    missing = [int(stamp) for stamp in wanted if int(stamp) not in lookup]
    if missing:
        raise SchemaError(
            f"the reference series at {path} is missing {len(missing)} of the "
            f"{wanted.size} grid periods, the first at "
            f"{np.datetime64(missing[0], 'ns')}; regime labels must align period by period"
        )
    return np.ascontiguousarray([lookup[int(stamp)] for stamp in wanted], dtype=np.float64)
