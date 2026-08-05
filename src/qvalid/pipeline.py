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
import math
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
from qvalid.adapters.timestamps import to_utc_nanos_from_pandas
from qvalid.adapters.tradelog import load_mapping, read_trade_log_csv
from qvalid.contracts import Basis, FloatArray, IntArray, Period, PeriodReturns, TrialMatrix
from qvalid.core.constants import DEFAULT_CONFIDENCE_LEVEL, PNL_RTOL
from qvalid.core.gridding import select_grid, trade_returns
from qvalid.core.metrics import period_metrics, trade_metrics
from qvalid.core.overfit import (
    deflated_sharpe_ratio,
    minimum_track_record_length,
    probability_of_backtest_overfitting,
)
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
        and the report says so, per D004. This is the size of the **search**,
        which can exceed the number of columns kept in ``trials_path``: someone
        who swept two hundred parameter sets and saved the best fifty searched
        two hundred, and the deflation has to know that.
    trials_path : str or None
        CSV with one timestamp column and one return column per configuration
        tested, on the same grid as the run. Supplies the dispersion across
        trial Sharpe ratios that the deflation needs and the full matrix that
        PBO needs. Without it the deflated Sharpe cannot run whatever
        ``n_trials`` says. See D052.
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
    trials_path: str | None = None
    reference_path: str | None = None
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
    verdict_requirements: tuple[str, ...] = DEFAULT_RANKING_REQUIREMENTS


def sha256_of(path: str | Path) -> str:
    r"""Hash a text input, with line endings normalised. See D050.

    The hash, not the path, is what identifies the data: two runs over files
    with the same hash are runs over the same data whatever the paths say.

    A line ending is a platform convention rather than data, so it is
    normalised before hashing. Without this, the same configuration checked out
    on Windows and on Linux produces two different values in the provenance,
    and the field stops answering the one question it exists to answer. Found
    by the CI matrix, where the Windows runner checks out CRLF.

    Parameters
    ----------
    path : str or pathlib.Path
        A text file: a trade log or a run configuration.

    Returns
    -------
    str
        Hex digest of the content with ``\r\n`` and lone ``\r`` reduced to
        ``\n``.
    """
    raw = Path(path).read_bytes()
    return hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


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


#: Below this the understatement of D051 is inside its own measurement error.
BLOCK_LENGTH_WARNING_THRESHOLD = 2.0


def _block_bootstrap_warning(block_length: float) -> tuple[str, ...]:
    """Warn that the simulated drawdown is understated under serial dependence.

    The stationary bootstrap joins blocks independently, so dependence is
    broken at every seam and the resampled paths trend less than the original
    series does. Drawdown is the statistic most sensitive to trending, so it
    comes out too small, and the direction is the dangerous one: the quantile
    someone would size capital from is optimistic, and the observed drawdown is
    placed higher in the distribution than it belongs.

    Measured against a conditional null, 24 series of 750 periods each, ratio
    of simulated to true median drawdown: 1.001 at block length 1.2, 0.954 at
    3.9, 0.940 at 7.4, 0.926 at 11.3. See D051.

    Silence would be the wrong default here. The number is printed prominently
    and read by someone deciding how much to risk.
    """
    if block_length <= BLOCK_LENGTH_WARNING_THRESHOLD:
        return ()
    return (
        f"the estimated block length is {block_length:.2f}, so the returns carry serial "
        "dependence; the stationary bootstrap breaks dependence at every block join and "
        "the simulated drawdown is therefore understated. Measured understatement of the "
        "median: about 5 per cent at block length 4, 6 per cent at 7, 7 per cent at 11, "
        "with the 95th percentile understated by slightly more. Treat the quantiles as a "
        "lower bound rather than a central estimate. See D051.",
    )


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
    # Normalise once, at the boundary. The signature promises ``str | Path``
    # and everything below is free to use path methods. Before this line the
    # provenance called ``.name`` on a value that could be a string, which is a
    # crash on the documented public signature that no test reached because
    # every caller in the repository happened to pass a Path. See D046.
    log_path = Path(log_path)
    config_path = Path(config_path)

    config = load_config(config_path)
    base = config_path.resolve().parent
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
                    [distribution.quantiles[q] for q in sorted(distribution.quantiles)] * 2,
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

        panel.append(
            _section(
                "drawdown_distribution",
                _drawdown,
                warnings=_block_bootstrap_warning(bootstrap.block_length.block_length),
            )
        )

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

    # An optional input that fails must not take the report down with it. The
    # module docstring promises a typed failure becomes an Evidence entry, and
    # ``_section`` delivers that for every computation; the **inputs** were
    # outside it, so a trial matrix off the grid aborted the run and the person
    # lost the metrics, the risk section and the regimes over one bad file.
    # See D053.
    risk_free_per_period = sharpe.risk_free_rate_per_period

    def _track_record() -> dict[str, Any]:
        """``02`` section 3.2, which lived in ``core`` and never reached a report.

        It needs no trial matrix, so unlike the deflation it can run on any
        input, and it answers a question the reader has: how long a record with
        these moments would have to be before this Sharpe is distinguishable
        from the benchmark. When the observed Sharpe is below the benchmark the
        honest answer is that no length suffices, and the section fails saying
        exactly that rather than printing a large finite number.
        """
        length = minimum_track_record_length(
            returns,
            risk_free_rate=config.risk_free_rate,
            target_probability=config.confidence_level,
        )
        return {
            "periods": _number(length.periods),
            "years": _number(length.years),
            "observed_periods": returns.values.size,
            "benchmark_sharpe": 0.0,
            "target_probability": config.confidence_level,
        }

    panel.append(_section("track_record", _track_record))

    trials, trials_error = None, None
    try:
        trials = _load_trials(base, config, returns)
    except QvalError as exc:
        trials_error = f"{type(exc).__name__}: {exc}"

    if trials_error is not None:
        for name in ("deflated_sharpe", "pbo"):
            panel.append(Evidence(name=name, status=EvidenceStatus.FAILED, reason=trials_error))
    elif config.n_trials is None:
        panel.append(
            Evidence(
                name="deflated_sharpe",
                status=EvidenceStatus.NOT_REQUESTED,
                reason="the number of configurations tested was not declared, so no "
                "correction for search was applied; estimating it would fabricate the "
                "input that determines the result, see D004",
            )
        )
    elif trials is None:
        panel.append(
            Evidence(
                name="deflated_sharpe",
                status=EvidenceStatus.NOT_REQUESTED,
                reason="a trial count was declared but the matrix of all tested "
                "configurations was not supplied; the deflation needs the dispersion "
                "across trial Sharpe ratios, which a single log cannot provide. "
                "Declare trials_path, see D052",
            )
        )
    else:
        declared, kept = config.n_trials, len(trials.config_ids)
        incoherent = (
            (
                f"the configuration declares {declared} trials and the matrix carries "
                f"{kept} columns; a search cannot be smaller than what it produced, so "
                "one of the two numbers is wrong and the deflation used the larger",
            )
            if declared < kept
            else ()
        )

        def _deflated() -> dict[str, Any]:
            # Excess on both sides. 02 section 1.2 defines the Sharpe on excess
            # returns, so a deflation computed on raw ones is a second quantity
            # travelling under the same name. On the real data example the two
            # disagreed in sign: the headline Sharpe read -0.26 while the
            # deflation reported a 93 per cent chance the true Sharpe was
            # positive, and both numbers were internally correct. See D055.
            excess = np.asarray(returns.values, dtype=np.float64) - risk_free_per_period
            sharpes = _trial_sharpe_ratios(trials, risk_free_per_period)
            estimate = deflated_sharpe_ratio(
                excess,
                n_trials=max(declared, kept),
                trial_variance=float(np.var(sharpes, ddof=1)),
            )
            return {
                "probability": _number(estimate.probability),
                "probability_against_zero": _number(estimate.psr_against_zero),
                "expected_maximum": _number(estimate.expected_maximum),
                "n_trials_declared": declared,
                "n_trials_in_matrix": kept,
                "trial_variance": _number(estimate.trial_variance),
                "trial_sharpe_best": _number(float(sharpes.max())),
                "trial_sharpe_median": _number(float(np.median(sharpes))),
            }

        panel.append(_section("deflated_sharpe", _deflated, warnings=incoherent))

        def _pbo() -> dict[str, Any]:
            result = probability_of_backtest_overfitting(trials)
            return {
                "probability": _number(result.probability),
                "median_logit": _number(result.median_logit),
                "logit_ceiling": _number(math.log(len(trials.config_ids))),
                "n_combinations": result.n_combinations,
                "n_splits": result.n_splits,
            }

        panel.append(_section("pbo", _pbo))

    # Same reason as the trial matrix above, and the same defect since v0.6: a
    # reference series misaligned by one session used to abort everything.
    reference, reference_error = None, None
    try:
        reference = _load_reference(base, config, np.asarray(returns.period_end_ns))
    except QvalError as exc:
        reference_error = f"{type(exc).__name__}: {exc}"

    if reference_error is not None:
        panel.append(Evidence(name="regimes", status=EvidenceStatus.FAILED, reason=reference_error))
    elif reference is None:
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
                    # Excess of the risk free alternative over the same horizon,
                    # accumulated additively because the basis is a fixed initial
                    # capital. The certainty equivalent then answers the question
                    # the person actually faces, which is whether to prefer this
                    # to holding cash, and not whether the number is above zero.
                    # On the real data example the raw form gave +0.24 while the
                    # headline Sharpe was -0.26: the verdict flattering a
                    # strategy the rest of the report had already refused. That
                    # is the defect 02 section 7 exists to prevent. See D055.
                    outcomes=terminal_return(bootstrap.paths)
                    - risk_free_per_period * returns.values.size,
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
            (
                np.asarray(returns.values).cumsum() * config.initial_capital
                + config.initial_capital
            ).tolist(),
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


def _trial_sharpe_ratios(trials: TrialMatrix, risk_free_per_period: float) -> FloatArray:
    """Per period **excess** Sharpe of every column, on the run's own conventions.

    Three conventions have to match the observed strategy, not one, and each
    was found the hard way:

    Per period and not annualised. ``02`` section 3 says so and so does the
    docstring of :func:`~qvalid.core.overfit.deflated_sharpe_ratio`. An
    annualised Sharpe here against a per period observed one is wrong by the
    square root of the periods per year.

    The run's basis, which the caller supplies by writing the trials file that
    way, because the matrix carries the basis as a declared field.

    And **excess of the same risk free rate**. This is the one that got past a
    first reading of the real data example: the observed Sharpe came out at
    -0.26 while the deflation reported a 93 per cent chance the true Sharpe was
    positive, because the trials had been written gross of a risk free rate of
    4.5 per cent and the observed strategy was measured net of it. Subtracting
    here, in the one place that compares the two, means the trials file is
    always raw returns and the convention is applied once. See D055.
    """
    values = np.asarray(trials.values, dtype=np.float64) - risk_free_per_period
    dispersion = values.std(axis=0, ddof=1)
    return np.asarray(np.where(dispersion > 0.0, values.mean(axis=0) / dispersion, 0.0))


def _load_trials(base: Path, config: RunConfig, returns: PeriodReturns) -> TrialMatrix | None:
    """Read the matrix of every configuration tested, aligned to the grid by timestamp.

    Until D052 there was no way to get one of these into a run. ``n_trials``
    could be declared and the deflated Sharpe still did not run, because the
    deflation needs the dispersion **across** trial Sharpe ratios and a single
    trade log cannot supply it. The section that ``02`` calls the one that
    separates this project from a spreadsheet of metrics was unreachable from
    the tool's own entry point.

    Expected format: a header row, the first column an ISO 8601 timezone aware
    timestamp of the period close, and one further column per configuration
    named by its identifier. Every configuration is a return series on the
    **same grid** as the run, which is the precondition of ``02`` section 3 and
    the reason :class:`~qvalid.contracts.TrialMatrix` declares the grid once for
    the whole matrix rather than per column. See D024.

    Alignment is by exact timestamp match, for the reason given in
    :func:`_load_reference`: a positional read would hand the overfitting tests
    a matrix that looks aligned and is not.

    Raises
    ------
    SchemaError
        Missing file, fewer than two configurations, or any grid period absent
        from the file.
    """
    if config.trials_path is None:
        return None
    path = base / config.trials_path
    if not path.is_file():
        raise SchemaError(f"trial matrix not found at {path}")

    frame = pd.read_csv(path)
    if frame.shape[1] < 3:
        raise SchemaError(
            f"the trial matrix at {path} needs a timestamp column and at least two "
            "configurations; the deflation measures dispersion across trials and one "
            "column has none"
        )
    stamps = to_utc_nanos_from_pandas(pd.to_datetime(frame.iloc[:, 0], utc=False), source=str(path))
    wanted = np.asarray(returns.period_end_ns, dtype=np.int64)
    position = {int(stamp): index for index, stamp in enumerate(stamps)}
    missing = [int(stamp) for stamp in wanted if int(stamp) not in position]
    if missing:
        raise SchemaError(
            f"the trial matrix at {path} is missing {len(missing)} of the {wanted.size} "
            f"grid periods, the first at {np.datetime64(missing[0], 'ns')}; every "
            "configuration must be measured on the same grid as the run, see 02 section 3"
        )
    rows = np.array([position[int(stamp)] for stamp in wanted], dtype=np.int64)
    values = np.ascontiguousarray(
        frame.iloc[:, 1:].to_numpy(dtype=np.float64)[rows, :], dtype=np.float64
    )
    return TrialMatrix(
        values=values,
        config_ids=np.asarray([str(name) for name in frame.columns[1:]], dtype=np.str_),
        period_end_ns=np.ascontiguousarray(wanted, dtype=np.int64),
        period=returns.period,
        periods_per_year=returns.periods_per_year,
        calendar_id=returns.calendar_id,
        basis=returns.basis,
        initial_capital=returns.initial_capital,
    )


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
    lookup = dict(
        zip(
            to_utc_nanos_from_pandas(stamps, source=str(path)),
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
