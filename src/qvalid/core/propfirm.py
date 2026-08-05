"""Proprietary desk evaluation, as a barrier model over daily paths.

``02`` section 6 fixes both the model and its constraint. The rules are daily,
so the paths must be daily: an evaluation measured in trades is not an
evaluation, for the same reason a horizon measured in trades is not a horizon.
The unit guard is the first thing this module does.

The rules of each desk live in a versioned YAML file and never in the code. They
change often, and the barrier logic is identical across desks, so a desk in code
would mean editing a tested module to track a marketing page. Three desks ship
with the library as fixtures, and adding a fourth touches no Python.

What the model is
-----------------
An account starts at the desk's size. Each day the strategy's realised P&L is
added. Four barriers apply, and the order they are checked in matters because
the reason a run died is part of the answer:

1. Daily loss limit, checked on the day's P&L alone.
2. Maximum loss, either static from the starting balance or trailing from the
   running peak.
3. Profit target, which passes the evaluation, but only once the minimum number
   of trading days has been met.
4. A calendar limit on the evaluation, when the desk imposes one.

After passing, the account is funded: it resets to the desk's size, the same
loss limits apply, and profit above the size is paid out on a cycle at the
declared split.

What the model is not
---------------------
It says nothing about whether the strategy has an edge; ``core/overfit.py``
answers that. It assumes the paths are a valid model of the process, which
requires everything ``core/resample.py`` requires. And it assumes the desk
honours its own written rules, which is an assumption about a counterparty and
not about a distribution.

The strategy's own capital is deliberately **not** used. Daily P&L is taken from
the paths in account currency and applied to the desk's account size, so the
question answered is "this strategy, at this size, on a desk of that size",
which is the question a trader actually has. Scaling the strategy is done by
resampling at a different capital, not by a factor hidden here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from qvalid.contracts import BoolArray, EquityPaths, FloatArray, IntArray, Period, Unit
from qvalid.core.constants import DEFAULT_CONFIDENCE_LEVEL
from qvalid.exceptions import InsufficientSampleError, SchemaError, UnitMismatchError

__all__ = [
    "EvaluationOutcome",
    "FundedRules",
    "PropFirmResult",
    "PropFirmRules",
    "evaluate",
    "load_rules",
]

_NOT_SET = -1
"""Sentinel for a day index that never happened."""


class EvaluationOutcome:
    """Enumeration of how a path ended, as integer codes for vectorised storage.

    Kept as plain integers rather than an enum because the outcome of ten
    thousand paths is an array, and the reason a run died is as informative as
    whether it died. A desk whose failures are all daily loss limit is a
    different problem from one whose failures are all trailing drawdown.
    """

    RUNNING = 0
    PASSED = 1
    FAILED_DAILY_LOSS = 2
    FAILED_MAX_LOSS = 3
    FAILED_TIME_LIMIT = 4
    UNFINISHED = 5

    LABELS: ClassVar[Mapping[int, str]] = MappingProxyType(
        {
            0: "running",
            1: "passed",
            2: "failed_daily_loss",
            3: "failed_max_loss",
            4: "failed_time_limit",
            5: "unfinished",
        }
    )


class FundedRules(BaseModel):
    """Rules that apply after the evaluation is passed.

    Attributes
    ----------
    profit_split : float
        Fraction of profit the trader keeps, in ``(0, 1]``.
    payout_cycle_days : int
        Trading days between payout opportunities.
    min_payout : float
        Below this, the cycle passes without paying and the profit carries.
    max_loss : float
        Loss from the starting balance, or from the peak under trailing mode,
        that closes the funded account.
    trailing : bool
        Whether ``max_loss`` is measured from the running peak.
    daily_loss_limit : float or None
        ``None`` means the desk imposes none on the funded account.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profit_split: float = Field(gt=0.0, le=1.0)
    payout_cycle_days: int = Field(gt=0)
    max_loss: float = Field(gt=0.0)
    min_payout: float = Field(default=0.0, ge=0.0)
    trailing: bool = False
    daily_loss_limit: float | None = Field(default=None, gt=0.0)


class PropFirmRules(BaseModel):
    """One desk, declared in a versioned YAML file.

    Attributes
    ----------
    rules_id : str
        Enters the report, so which rule set produced a number is legible.
    account_size : float
    profit_target : float
        Absolute profit that passes the evaluation.
    max_loss : float
        Absolute loss that fails it, from the starting balance or from the peak.
    trailing : bool
        ``True`` measures ``max_loss`` from the running peak. This is the single
        most consequential field on the whole form and desks differ on it.
    daily_loss_limit : float or None
    min_trading_days : int
        Days with non zero P&L required before the target can pass. A desk with
        this rule is refusing to fund someone who got there in two lucky days.
    max_evaluation_days : int or None
        Calendar limit on the evaluation.
    evaluation_fee : float
        What the attempt costs. Enters the expected value directly.
    fee_refunded_on_pass : bool
    funded : FundedRules

    Notes
    -----
    ``02`` section 6 requires this to be a file. The barrier logic is the same
    for every desk, and the numbers change with the marketing calendar, so a
    desk written in Python would mean editing a tested module to track a web
    page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules_id: str
    account_size: float = Field(gt=0.0)
    profit_target: float = Field(gt=0.0)
    max_loss: float = Field(gt=0.0)
    funded: FundedRules
    trailing: bool = False
    daily_loss_limit: float | None = Field(default=None, gt=0.0)
    min_trading_days: int = Field(default=0, ge=0)
    max_evaluation_days: int | None = Field(default=None, gt=0)
    evaluation_fee: float = Field(default=0.0, ge=0.0)
    fee_refunded_on_pass: bool = False

    @model_validator(mode="after")
    def _target_is_reachable_before_the_limit(self) -> PropFirmRules:
        """Refuse a rule set that cannot be passed even in principle.

        A daily loss limit above the maximum loss is not a constraint, and a
        minimum number of days above the calendar limit is unsatisfiable. Both
        are transcription errors rather than exotic desks, and catching them at
        load time is cheaper than reading a probability of zero and wondering.
        """
        if self.daily_loss_limit is not None and self.daily_loss_limit > self.max_loss:
            raise ValueError(
                f"daily_loss_limit {self.daily_loss_limit} exceeds max_loss "
                f"{self.max_loss}, so it can never bind and is probably a transcription error"
            )
        if (
            self.max_evaluation_days is not None
            and self.min_trading_days > self.max_evaluation_days
        ):
            raise ValueError(
                f"min_trading_days {self.min_trading_days} exceeds max_evaluation_days "
                f"{self.max_evaluation_days}, so the evaluation cannot be passed"
            )
        return self


def load_rules(path: str | Path) -> PropFirmRules:
    """Load and validate one desk's rules from YAML.

    Raises
    ------
    SchemaError
        Missing file, malformed YAML, or any field failing validation.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SchemaError(f"proprietary desk rules not found at {file_path}")
    try:
        raw: Any = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchemaError(f"desk rules at {file_path} are not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError(f"desk rules at {file_path} must be a mapping, got {type(raw).__name__}")
    try:
        return PropFirmRules.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"desk rules at {file_path} are invalid: {exc}") from exc


@dataclass(frozen=True, slots=True)
class PropFirmResult:
    """What a desk's rules do to a distribution of strategy paths.

    Attributes
    ----------
    rules_id : str
    pass_probability, pass_standard_error : float
        Of clearing the evaluation. The standard error is binomial over paths.
    payout_probability : float
        Of reaching at least one payout, which is a stricter event than passing.
    expected_net_value : float
        Mean of total payouts minus the evaluation fee, over **all** paths
        including the ones that failed. This is the number that decides whether
        the attempt is worth making, and averaging only over the survivors would
        answer a different and flattering question.
    net_value_percentiles : dict of float to float
        Of the same per path quantity. The mean of a heavily skewed payoff is
        not the experience of a typical attempt.
    days_to_pass, days_to_first_payout : dict of float to float
        Percentiles, conditional on the event happening. Empty when it never
        does, because a percentile of an empty set is not zero.
    outcome_counts : dict of str to int
        How each path ended. A desk whose failures are all daily loss limit is a
        different problem from one whose failures are all trailing drawdown, and
        a single pass probability hides which.
    n_paths, horizon_days : int
    """

    rules_id: str
    pass_probability: float
    pass_standard_error: float
    payout_probability: float
    expected_net_value: float
    net_value_percentiles: dict[float, float]
    days_to_pass: dict[float, float]
    days_to_first_payout: dict[float, float]
    outcome_counts: dict[str, int]
    n_paths: int
    horizon_days: int


def _require_daily_paths(paths: EquityPaths) -> FloatArray:
    """Refuse anything but daily calendar paths.

    ``02`` section 6 is explicit: the rules are daily, so a simulator fed trade
    indexed paths would apply a daily loss limit to a quantity that has no day.
    The guard is the first thing the module does rather than the last, so a
    wrong call fails before spending a minute simulating.
    """
    if paths.unit is not Unit.PERIOD:
        raise UnitMismatchError(
            f"a proprietary desk simulation requires unit=PERIOD, got {paths.unit}; "
            "daily loss limits and minimum traded days have no meaning over a path "
            "indexed by trade number"
        )
    if paths.period is not Period.DAILY:
        raise UnitMismatchError(
            f"a proprietary desk simulation requires period=DAILY, got {paths.period}; "
            "the rules are daily and a weekly grid cannot express a daily loss limit"
        )
    values = np.asarray(paths.values, dtype=np.float64)
    if paths.n_steps < 2:
        raise InsufficientSampleError(
            "the simulation needs at least one day of P&L",
            observed=paths.n_steps,
            threshold=2,
        )
    return values


def evaluate(
    paths: EquityPaths,
    rules: PropFirmRules,
    *,
    percentiles: tuple[float, ...] = (0.05, 0.5, 0.95),
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> PropFirmResult:
    """Run the desk's barriers over every simulated path.

    Parameters
    ----------
    paths : EquityPaths
        Daily, calendar indexed. Only the day to day differences are used, so
        the strategy's own starting capital does not enter.
    rules : PropFirmRules
    percentiles : tuple of float, optional
    confidence_level : float, optional
        Carried for the report; the pass probability reports a binomial
        standard error rather than an interval, since the event is a Bernoulli
        over paths and the error is exact.

    Returns
    -------
    PropFirmResult

    Raises
    ------
    UnitMismatchError
        Paths that are not daily calendar paths.
    InsufficientSampleError
        Fewer than one day of P&L.

    Notes
    -----
    The evaluation and the funded phase share one walk over the horizon. A path
    that passes on day forty continues into the funded phase with the remaining
    days, so a short horizon understates payouts and the horizon is reported
    next to them.

    Order of checks within a day is deliberate and is the model, not an
    implementation detail. The daily loss limit is checked before the maximum
    loss, because a day that breaches both is recorded as a daily loss breach,
    which is what the desk would say. The profit target is checked last, so a
    day that reaches the target while breaching a limit is a failure. Desks
    differ on this and the choice is declared rather than assumed.
    """
    values = _require_daily_paths(paths)
    daily = np.diff(values, axis=1)
    n_paths, n_days = daily.shape

    size = rules.account_size
    equity: FloatArray = np.full(n_paths, size)
    peak: FloatArray = np.full(n_paths, size)
    traded: IntArray = np.zeros(n_paths, dtype=np.int64)
    outcome: IntArray = np.full(n_paths, EvaluationOutcome.RUNNING, dtype=np.int64)
    pass_day: IntArray = np.full(n_paths, _NOT_SET, dtype=np.int64)

    funded_equity: FloatArray = np.full(n_paths, np.nan)
    funded_peak: FloatArray = np.full(n_paths, np.nan)
    funded_since: IntArray = np.full(n_paths, _NOT_SET, dtype=np.int64)
    funded_alive: BoolArray = np.zeros(n_paths, dtype=bool)
    payouts: FloatArray = np.zeros(n_paths)
    first_payout_day: IntArray = np.full(n_paths, _NOT_SET, dtype=np.int64)

    daily_limit = rules.daily_loss_limit
    funded_daily_limit = rules.funded.daily_loss_limit

    for day in range(n_days):
        step = daily[:, day]

        evaluating = outcome == EvaluationOutcome.RUNNING
        if bool(evaluating.any()):
            equity = np.where(evaluating, equity + step, equity)
            traded = np.where(evaluating & (step != 0.0), traded + 1, traded)
            peak = np.where(evaluating, np.maximum(peak, equity), peak)

            floor = np.where(rules.trailing, peak - rules.max_loss, size - rules.max_loss)
            breached_daily = (
                evaluating & (step < -daily_limit)
                if daily_limit is not None
                else np.zeros(n_paths, dtype=bool)
            )
            breached_max = evaluating & ~breached_daily & (equity <= floor)
            reached = (
                evaluating
                & ~breached_daily
                & ~breached_max
                & (equity >= size + rules.profit_target)
                & (traded >= rules.min_trading_days)
            )
            outcome = np.where(breached_daily, EvaluationOutcome.FAILED_DAILY_LOSS, outcome)
            outcome = np.where(breached_max, EvaluationOutcome.FAILED_MAX_LOSS, outcome)
            outcome = np.where(reached, EvaluationOutcome.PASSED, outcome)
            pass_day = np.where(reached, day + 1, pass_day)

            if rules.max_evaluation_days is not None and day + 1 >= rules.max_evaluation_days:
                expired = outcome == EvaluationOutcome.RUNNING
                outcome = np.where(expired, EvaluationOutcome.FAILED_TIME_LIMIT, outcome)

            newly_funded = reached
            funded_equity = np.where(newly_funded, size, funded_equity)
            funded_peak = np.where(newly_funded, size, funded_peak)
            funded_since = np.where(newly_funded, day, funded_since)
            funded_alive = funded_alive | newly_funded

        if bool(funded_alive.any()):
            active = funded_alive & (funded_since < day)
            funded_equity = np.where(active, funded_equity + step, funded_equity)
            funded_peak = np.where(active, np.maximum(funded_peak, funded_equity), funded_peak)

            funded_floor = np.where(
                rules.funded.trailing,
                funded_peak - rules.funded.max_loss,
                size - rules.funded.max_loss,
            )
            blown_daily = (
                active & (step < -funded_daily_limit)
                if funded_daily_limit is not None
                else np.zeros(n_paths, dtype=bool)
            )
            blown = active & (blown_daily | (funded_equity <= funded_floor))
            funded_alive = funded_alive & ~blown

            elapsed = day - funded_since
            due = (
                funded_alive
                & active
                & (elapsed > 0)
                & (elapsed % rules.funded.payout_cycle_days == 0)
            )
            profit = np.where(due, funded_equity - size, 0.0)
            payable = np.where(profit > 0.0, profit * rules.funded.profit_split, 0.0)
            paying = due & (payable >= rules.funded.min_payout) & (payable > 0.0)
            payouts = np.where(paying, payouts + payable, payouts)
            funded_equity = np.where(paying, size, funded_equity)
            funded_peak = np.where(paying, size, funded_peak)
            first_payout_day = np.where(
                paying & (first_payout_day == _NOT_SET), day + 1, first_payout_day
            )

    outcome = np.where(outcome == EvaluationOutcome.RUNNING, EvaluationOutcome.UNFINISHED, outcome)
    passed = outcome == EvaluationOutcome.PASSED
    probability = float(passed.mean())
    fee = rules.evaluation_fee
    refund = np.where(passed & rules.fee_refunded_on_pass, fee, 0.0)
    net = payouts - fee + refund

    return PropFirmResult(
        rules_id=rules.rules_id,
        pass_probability=probability,
        pass_standard_error=float(np.sqrt(max(probability * (1.0 - probability), 0.0) / n_paths)),
        payout_probability=float((first_payout_day != _NOT_SET).mean()),
        expected_net_value=float(net.mean()),
        net_value_percentiles={q: float(np.quantile(net, q)) for q in percentiles},
        days_to_pass=_conditional_percentiles(pass_day, percentiles),
        days_to_first_payout=_conditional_percentiles(first_payout_day, percentiles),
        outcome_counts={
            EvaluationOutcome.LABELS[int(code)]: int(count)
            for code, count in zip(*np.unique(outcome, return_counts=True), strict=True)
        },
        n_paths=n_paths,
        horizon_days=n_days,
    )


def _conditional_percentiles(days: IntArray, percentiles: tuple[float, ...]) -> dict[float, float]:
    """Percentiles over the paths where the event happened, empty when none did.

    Conditional rather than unconditional, and stated as such on the result. An
    unconditional median of a variable that is undefined for most paths is a
    number that looks reassuring for the wrong reason.
    """
    reached = days[days != _NOT_SET]
    if reached.size == 0:
        return {}
    return {q: float(np.quantile(reached, q)) for q in percentiles}
