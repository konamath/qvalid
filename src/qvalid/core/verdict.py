"""The verdict: an evidence panel, and an ordering that refuses to score absence.

``02`` section 7 asks for two outputs in parallel and forbids collapsing them
into one. This module produces the second, the certainty equivalent under
cumulative prospect theory, and enforces the rule that binds it to the first.

The rule, restated because it is the whole point. A test suppressed by a
validity condition never enters the ordering as though it had passed. Absence of
evidence appears as absence. A strategy whose search correction did not run
because the number of trials was not declared is **not comparable** with one
whose did, and averaging them into a single ranking would launder the missing
correction into an endorsement.

That is enforced structurally: :func:`rank` returns two lists, ranked and
unrankable, and a candidate missing a required section can only ever be in the
second. There is no argument that moves it to the first, because the reason it
cannot be ranked is not a matter of taste.

Why a certainty equivalent and not a grade
-------------------------------------------
``01`` lists a single letter grade among the explicit non goals. The objection
is not that grades are imprecise, it is that they have no interpretation: an
"A" answers no question. A certainty equivalent does. It is the certain amount
that an agent with a declared utility and a declared probability weighting would
accept in place of the strategy's distribution of outcomes, so a ranking by it
means "an agent with these preferences prefers this one", and the preferences
are printed next to the number.

Cumulative prospect theory rather than expected utility, because the object
being ranked is a distribution of trading outcomes and the two features that
distinguish CPT are exactly the two that matter there: loss aversion, so a
drawdown is not a negative gain, and probability weighting, so the tail is not
discounted at its frequency.

References
----------
Tversky, A., and Kahneman, D. (1992). Advances in prospect theory: cumulative
representation of uncertainty. Journal of Risk and Uncertainty 5(4), 297-323.

Prelec, D. (1998). The probability weighting function. Econometrica 66(3),
497-527.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from qvalid.contracts import FloatArray
from qvalid.exceptions import InsufficientSampleError

__all__ = [
    "DEFAULT_RANKING_REQUIREMENTS",
    "Candidate",
    "CptParameters",
    "Ranking",
    "Verdict",
    "certainty_equivalent",
    "cpt_value",
    "decision_weights",
    "probability_weight",
    "rank",
]

DEFAULT_RANKING_REQUIREMENTS = ("resampling", "deflated_sharpe")
"""Sections that must have run before a strategy can be ranked.

``resampling`` because the distribution being valued comes from it, and
``deflated_sharpe`` because ``02`` section 7 forbids treating an uncorrected
result as comparable with a corrected one. Both are declared rather than
hardcoded into the ranking function, and the list used enters the report.
"""


class CptParameters(BaseModel):
    """Declared preferences. Nothing about the ranking is meaningful without them.

    Attributes
    ----------
    alpha : float
        Curvature of the value function over gains, in ``(0, 1]``.
    beta : float
        Curvature over losses.
    loss_aversion : float
        Multiplier on losses. One means none.
    gamma : float
        Probability weighting for gains, in ``(0, 1]``.
    delta : float
        Probability weighting for losses.
    reference : float
        The point outcomes are measured from. Zero means "compared with not
        trading". A risk free return would make the ranking answer a different
        and equally legitimate question, which is why it is a parameter rather
        than a constant.

    Notes
    -----
    Defaults are the estimates of Tversky and Kahneman (1992). They are
    **population averages from laboratory gambles**, not the preferences of any
    particular trader, and the ranking they produce is the ranking that
    hypothetical agent would make. Anyone using this to size real positions
    should supply their own and see how much the order moves; if it moves a lot,
    the ordering was never about the strategies.

    Setting ``alpha = beta = loss_aversion = gamma = delta = 1`` reduces the
    whole apparatus to the expected value, exactly. A test asserts that, because
    a framework that does not contain the neutral case as a special case is
    probably not computing what it claims.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    alpha: float = Field(default=0.88, gt=0.0, le=1.0)
    beta: float = Field(default=0.88, gt=0.0, le=1.0)
    loss_aversion: float = Field(default=2.25, ge=1.0)
    gamma: float = Field(default=0.61, gt=0.0, le=1.0)
    delta: float = Field(default=0.69, gt=0.0, le=1.0)
    reference: float = 0.0

    @property
    def is_neutral(self) -> bool:
        """True when the parameters reduce the model to the expected value."""
        return (
            self.alpha == 1.0
            and self.beta == 1.0
            and self.loss_aversion == 1.0
            and self.gamma == 1.0
            and self.delta == 1.0
        )


def probability_weight(probability: FloatArray | float, curvature: float) -> FloatArray:
    """Tversky and Kahneman (1992) weighting function.

    Parameters
    ----------
    probability : array of float or float
        Cumulative probability, in ``[0, 1]``.
    curvature : float
        ``1`` gives the identity.

    Returns
    -------
    numpy.ndarray of float64
        ``p^c / (p^c + (1-p)^c)^(1/c)``.

    Notes
    -----
    The input is clipped to ``[0, 1]`` before use, and that is not cosmetic. A
    cumulative probability built by summing equal weights overshoots one by a
    few times machine epsilon on the last step, ``1 - p`` goes slightly
    negative, and a negative base to a fractional power is ``nan``. The whole
    certainty equivalent then comes out ``nan`` for the most ordinary input
    there is, a certain outcome. It was found exactly that way.

    Shape. The function overweights small probabilities and underweights large
    ones, crossing at about 0.34 for the estimated curvature. That is the
    feature: it is why a one per cent chance of ruin is not discounted at one
    per cent.
    """
    values = np.clip(np.asarray(probability, dtype=np.float64), 0.0, 1.0)
    if curvature == 1.0:
        return values
    powered = values**curvature
    complement = (1.0 - values) ** curvature
    return np.asarray(powered / (powered + complement) ** (1.0 / curvature))


def decision_weights(outcomes: FloatArray, params: CptParameters) -> FloatArray:
    """Cumulative decision weights for a sample of equally likely outcomes.

    Parameters
    ----------
    outcomes : numpy.ndarray of float64
        Sorted ascending, already measured from the reference point.
    params : CptParameters

    Returns
    -------
    numpy.ndarray of float64
        One weight per outcome, in the same order.

    Notes
    -----
    Losses are accumulated from the worst upward and gains from the best
    downward, which is what makes the transformation cumulative rather than a
    reweighting of individual probabilities. The latter is the older prospect
    theory and violates stochastic dominance; the cumulative form does not.

    The weights **do not** sum to one, and that is correct rather than a bug.
    Under a subadditive weighting function the gain weights sum to ``w+`` of the
    probability of a gain and the loss weights to ``w-`` of the probability of a
    loss, and those two need not add to one. The gap is the certainty effect.
    """
    n_obs = outcomes.size
    step = 1.0 / n_obs
    weights = np.zeros(n_obs, dtype=np.float64)
    negative = outcomes < 0.0

    n_losses = int(negative.sum())
    if n_losses:
        cumulative = np.arange(1, n_losses + 1, dtype=np.float64) * step
        weighted = probability_weight(cumulative, params.delta)
        weights[:n_losses] = np.diff(np.concatenate(([0.0], weighted)))

    n_gains = n_obs - n_losses
    if n_gains:
        cumulative = np.arange(1, n_gains + 1, dtype=np.float64) * step
        weighted = probability_weight(cumulative, params.gamma)
        weights[n_losses:] = np.diff(np.concatenate(([0.0], weighted)))[::-1]
    return weights


def cpt_value(outcomes: FloatArray, params: CptParameters | None = None) -> float:
    """Prospect theory value of a sample of outcomes.

    Parameters
    ----------
    outcomes : numpy.ndarray of float64
        Terminal returns, or any distribution of outcomes on one scale.
    params : CptParameters or None, optional
        ``None`` uses the Tversky and Kahneman estimates, which are declared on
        the result rather than assumed by the reader.

    Returns
    -------
    float

    Raises
    ------
    InsufficientSampleError
        Empty sample, or any non finite outcome. A non finite outcome here
        would propagate silently into the ordering.
    """
    settings = params or CptParameters()
    values = np.asarray(outcomes, dtype=np.float64)
    if values.size == 0:
        raise InsufficientSampleError(
            "the certainty equivalent needs at least one outcome", observed=0, threshold=1
        )
    if not bool(np.all(np.isfinite(values))):
        raise InsufficientSampleError(
            "every outcome must be finite; a non finite one would propagate into the "
            "ordering without anything looking wrong",
            observed="non finite",
            threshold="finite",
        )
    centred = np.sort(values - settings.reference)
    utility = np.where(
        centred >= 0.0,
        np.abs(centred) ** settings.alpha,
        -settings.loss_aversion * np.abs(centred) ** settings.beta,
    )
    return float((decision_weights(centred, settings) * utility).sum())


def certainty_equivalent(outcomes: FloatArray, params: CptParameters | None = None) -> float:
    """Certain outcome an agent with these preferences would accept instead.

    Parameters
    ----------
    outcomes : numpy.ndarray of float64
    params : CptParameters or None, optional

    Returns
    -------
    float
        On the same scale as the outcomes, and back on the original scale rather
        than measured from the reference point, so it can be read directly.

    Notes
    -----
    The inverse of the value function, applied to the prospect theory value:
    ``V^(1/alpha)`` for a non negative value and ``-(-V/lambda)^(1/beta)``
    otherwise.

    Properties worth knowing, all of them asserted by tests. A certain outcome
    has itself as its certainty equivalent. A symmetric gamble around zero has a
    **negative** certainty equivalent under loss aversion, which is the whole
    reason to use this rather than a mean. Adding a constant to every outcome
    raises the certainty equivalent, so first order stochastic dominance is
    respected. And with neutral parameters the certainty equivalent is exactly
    the arithmetic mean.
    """
    settings = params or CptParameters()
    value = cpt_value(outcomes, settings)
    if value >= 0.0:
        equivalent = value ** (1.0 / settings.alpha)
    else:
        equivalent = -((-value / settings.loss_aversion) ** (1.0 / settings.beta))
    return float(equivalent + settings.reference)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One strategy to be ranked, with the evidence that decides whether it can be.

    Attributes
    ----------
    name : str
    outcomes : numpy.ndarray of float64
        Distribution of outcomes, normally the terminal returns of the
        simulated paths.
    sections_run : tuple of str
        Names of the evidence sections that produced a result.
    sections_absent : mapping of str to str
        Names of the sections that did not, mapped to why. Taken straight from
        a :class:`~qvalid.report.model.ValidationReport`, so the ranking and the
        report cannot disagree about what ran.
    """

    name: str
    outcomes: FloatArray
    sections_run: tuple[str, ...] = ()
    sections_absent: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Verdict:
    """One strategy's place in the ordering, or the reason it has none.

    Attributes
    ----------
    name : str
    certainty_equivalent : float or None
        ``None`` exactly when the strategy is not rankable. There is no state
        in which a number exists without the panel behind it having qualified,
        which is the structural form of the rule in ``02`` section 7.
    cpt_value : float or None
    rankable : bool
    blocking_sections : tuple of str
        Required sections that did not run. Empty when rankable.
    reason : str or None
        Why it cannot be ranked, in the same vocabulary the report uses.
    parameters : CptParameters
        Carried on every verdict, because a ranking without the preferences that
        produced it is a ranking nobody can reproduce.
    """

    name: str
    certainty_equivalent: float | None
    cpt_value: float | None
    rankable: bool
    blocking_sections: tuple[str, ...]
    reason: str | None
    parameters: CptParameters

    def __post_init__(self) -> None:
        if self.rankable != (self.certainty_equivalent is not None):
            raise ValueError(
                f"verdict for {self.name!r} is inconsistent: a certainty equivalent exists "
                "exactly when the strategy is rankable, see 02 section 7"
            )
        if not self.rankable and not self.reason:
            raise ValueError(
                f"verdict for {self.name!r} is unrankable but states no reason; absence of "
                "evidence must say why"
            )


@dataclass(frozen=True, slots=True)
class Ranking:
    """The two lists, kept apart on purpose.

    Attributes
    ----------
    ranked : tuple of Verdict
        Best first, by certainty equivalent.
    unrankable : tuple of Verdict
        In the order supplied. Never interleaved with the ranked ones, because
        interleaving is exactly the laundering ``02`` section 7 forbids: it
        would put a strategy with no search correction next to one that has it
        as though the two were comparable.
    requirements : tuple of str
        The sections that had to run, declared and reported.
    parameters : CptParameters
    """

    ranked: tuple[Verdict, ...]
    unrankable: tuple[Verdict, ...]
    requirements: tuple[str, ...]
    parameters: CptParameters

    @property
    def best(self) -> Verdict | None:
        """The top of the ordering, or ``None`` when nothing could be ranked."""
        return self.ranked[0] if self.ranked else None

    def to_dict(self) -> dict[str, Any]:
        """Plain mapping for the report, keys sorted by the serialiser."""
        return {
            "parameters": self.parameters.model_dump(),
            "ranked": [
                {
                    "name": v.name,
                    "certainty_equivalent": v.certainty_equivalent,
                    "cpt_value": v.cpt_value,
                }
                for v in self.ranked
            ],
            "requirements": list(self.requirements),
            "unrankable": [
                {"name": v.name, "blocking_sections": list(v.blocking_sections), "reason": v.reason}
                for v in self.unrankable
            ],
        }


def rank(
    candidates: list[Candidate],
    *,
    params: CptParameters | None = None,
    requirements: tuple[str, ...] = DEFAULT_RANKING_REQUIREMENTS,
) -> Ranking:
    """Order strategies by certainty equivalent, refusing to rank incomplete evidence.

    Parameters
    ----------
    candidates : list of Candidate
    params : CptParameters or None, optional
    requirements : tuple of str, optional
        Sections that must have run. Declared rather than hardcoded, and the
        list used is carried on the result.

    Returns
    -------
    Ranking

    Notes
    -----
    A candidate missing any required section goes to ``unrankable`` with the
    names of the missing sections, and no certainty equivalent is computed for
    it at all. Computing one and then hiding it would leave the number available
    to anyone who looked, and the point of the rule is that it should not exist.

    Ties are broken by name, so the ordering is deterministic. Two strategies
    with the same certainty equivalent are the same to the agent whose
    preferences were declared, and inventing a tiebreak would be inventing a
    preference.
    """
    settings = params or CptParameters()
    ranked: list[Verdict] = []
    unrankable: list[Verdict] = []

    for candidate in candidates:
        missing = tuple(name for name in requirements if name not in candidate.sections_run)
        if missing:
            detail = "; ".join(
                f"{name}: {candidate.sections_absent.get(name, 'not present in the panel')}"
                for name in missing
            )
            unrankable.append(
                Verdict(
                    name=candidate.name,
                    certainty_equivalent=None,
                    cpt_value=None,
                    rankable=False,
                    blocking_sections=missing,
                    reason=(
                        f"required sections did not run, so this strategy is not comparable "
                        f"with one whose did: {detail}"
                    ),
                    parameters=settings,
                )
            )
            continue
        value = cpt_value(candidate.outcomes, settings)
        ranked.append(
            Verdict(
                name=candidate.name,
                certainty_equivalent=certainty_equivalent(candidate.outcomes, settings),
                cpt_value=value,
                rankable=True,
                blocking_sections=(),
                reason=None,
                parameters=settings,
            )
        )

    ranked.sort(key=lambda v: (-(v.certainty_equivalent or 0.0), v.name))
    return Ranking(
        ranked=tuple(ranked),
        unrankable=tuple(unrankable),
        requirements=requirements,
        parameters=settings,
    )
