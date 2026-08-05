"""Typed exceptions raised across the package.

Every exception defined here carries the observed value and the violated
threshold in the message body, not only a description of the condition. See
``04_convencoes_de_codigo.md``. Raising a bare ``ValueError`` on a foreseen
condition is prohibited.

This module is a leaf: it imports nothing from the package, so both
``adapters`` and ``core`` may import it without creating a cycle.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CalendarCoverageError",
    "GridSparsityError",
    "InsufficientSampleError",
    "LookaheadError",
    "QvalError",
    "RegimeSparsityError",
    "SchemaError",
    "ThresholdViolation",
    "TradeIntegrityError",
    "UnitMismatchError",
]


class QvalError(Exception):
    """Base class for every error raised by ``qvalid``."""


class ThresholdViolation(QvalError):
    """Base for errors that compare an observed quantity against a threshold.

    Parameters
    ----------
    message : str
        Description of the violated condition.
    observed : Any
        The value actually measured.
    threshold : Any
        The limit that was violated.
    detail : str, optional
        Extra context appended verbatim, for example the identifiers of the
        offending records.

    Notes
    -----
    The formatted message always contains both ``observed`` and ``threshold``.
    A test in ``tests/unit/test_exceptions.py`` enforces this, so the guarantee
    is machine checked rather than a convention.
    """

    def __init__(
        self,
        message: str,
        *,
        observed: Any,
        threshold: Any,
        detail: str = "",
    ) -> None:
        self.observed = observed
        self.threshold = threshold
        self.detail = detail
        body = f"{message} (observed={observed!r}, threshold={threshold!r})"
        if detail:
            body = f"{body} {detail}"
        super().__init__(body)


class SchemaError(QvalError):
    """Unexpected schema in an external source, or a malformed contract.

    Raised when the *shape* of the data is wrong: missing column, mismatched
    array lengths, wrong dtype, naive timestamp. Value level invariants raise
    :class:`TradeIntegrityError` instead.
    """


class TradeIntegrityError(ThresholdViolation):
    """A ``TradeLog`` invariant is violated, including P&L coherence.

    See ``01_escopo_e_arquitetura.md`` for the identity and the tolerance rule,
    and D007 for why the check lives at the adapter boundary.
    """


class InsufficientSampleError(ThresholdViolation):
    """Fewer trades or periods than the declared minimum.

    See ``02_especificacao_matematica.md`` section 1.4.
    """


class GridSparsityError(ThresholdViolation):
    """No grid on the ladder satisfies the three conditions of the spec.

    See ``02_especificacao_matematica.md`` section 1.1. Raised instead of
    silently returning a Sharpe ratio that measures period count rather than
    performance.
    """


class CalendarCoverageError(ThresholdViolation):
    """The trading calendar does not span the exit timestamps of the log.

    Distinct from :class:`GridSparsityError`, which is about the ladder being
    infeasible on a calendar that does cover the sample. This one says the two
    contracts describe different intervals of time, so no grid built from the
    calendar can attribute the log correctly.

    Raised rather than silently clamping, because clamping would attribute
    every out of range trade to the boundary period and manufacture a spike
    exactly where the sample is least trustworthy.
    """


class LookaheadError(QvalError):
    """A labelling statistic used information not available at the timestamp.

    See ``02_especificacao_matematica.md`` section 4.
    """


class RegimeSparsityError(ThresholdViolation):
    """A regime state holds fewer observations than the declared minimum.

    See ``02_especificacao_matematica.md`` section 2.2.
    """


class UnitMismatchError(QvalError):
    """``EquityPaths`` unit is incompatible with the consuming function.

    Calendar anchored questions, such as risk of ruin over a declared horizon
    or proprietary desk daily loss limits, require ``Unit.PERIOD``. A horizon
    measured in trades is not a horizon.
    """
