"""The report contract, and the evidence panel that makes absence explicit.

``ValidationReport`` lives here rather than in ``contracts.py`` for a dependency
reason, not a taxonomic one. It aggregates ``PeriodMetrics``, ``PboResult``,
``RegimeAttribution`` and their siblings, all of which live in ``core``, and
``core`` imports ``contracts``. Placing the aggregate among the contracts would
make ``contracts`` import ``core`` and close a cycle. The dependency rule of
``01`` points inward, so the type that collects results belongs to the layer
that consumes them. See D029.

The conceptual centre of this module is :class:`Evidence`. ``02`` section 7
forbids a suppressed test from entering the verdict as though it had passed, and
requires absence of evidence to appear as absence rather than as silent
approval. That is enforced structurally here: an evidence entry carries either a
result or a reason for its absence, and there is no representable state with
neither. The reasons are enumerated rather than free text, so the report layer
can render them and the verdict layer of v0.9 can refuse to score them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Evidence",
    "EvidenceStatus",
    "RunProvenance",
    "ValidationReport",
]


class EvidenceStatus(StrEnum):
    """Why a section of the panel holds what it holds.

    ``RAN``
        The test ran and the result is present.
    ``SUPPRESSED``
        A validity condition of ``02`` section 1.4 was violated, so the test was
        not run. The observed value and the threshold travel with it.
    ``NOT_REQUESTED``
        An input only the user can supply was not supplied. The deflated Sharpe
        ratio without a declared number of trials is the case D004 describes:
        the report states that no correction for search was applied rather than
        inventing the number.
    ``FAILED``
        A typed error was raised while running. Kept as evidence rather than
        crashing the report, because a report that dies on one section teaches
        less than one that shows which section died.
    """

    RAN = "RAN"
    SUPPRESSED = "SUPPRESSED"
    NOT_REQUESTED = "NOT_REQUESTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One entry of the panel: a result, or the reason there is none.

    Attributes
    ----------
    name : str
        Stable identifier of the section, used as a key in the serialisation.
    status : EvidenceStatus
    payload : mapping or None
        Present exactly when ``status`` is ``RAN``.
    reason : str or None
        Present exactly when ``status`` is not ``RAN``.
    observed, threshold : Any or None
        Carried when the reason is a violated threshold, so the reader sees how
        far off the sample was rather than only that it was.
    warnings : tuple of str
        Warnings emitted by the computation itself, which travel even when the
        section ran.

    Raises
    ------
    ValueError
        If the invariant is broken, that is a ``RAN`` entry without a payload or
        a non ``RAN`` entry without a reason. This is a programming error rather
        than a foreseen data condition, so it is not one of the typed
        exceptions of ``04``.

    Notes
    -----
    The invariant is the point. Without it a section could carry neither result
    nor reason, and a reader would have no way to tell "we tested and found
    nothing" from "we never tested". ``02`` section 7 names that confusion as
    the defect the panel exists to prevent.
    """

    name: str
    status: EvidenceStatus
    payload: dict[str, Any] | None = None
    reason: str | None = None
    observed: Any = None
    threshold: Any = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is EvidenceStatus.RAN:
            if self.payload is None:
                raise ValueError(f"evidence {self.name!r} ran but carries no payload")
            if self.reason is not None:
                raise ValueError(f"evidence {self.name!r} ran but also carries a reason")
        else:
            if self.reason is None:
                raise ValueError(
                    f"evidence {self.name!r} is {self.status} but carries no reason; "
                    "absence of evidence must state why, see 02 section 7"
                )
            if self.payload is not None:
                raise ValueError(f"evidence {self.name!r} did not run but carries a payload")

    @property
    def ran(self) -> bool:
        """True when the section produced a result."""
        return self.status is EvidenceStatus.RAN


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """Everything needed to reproduce the run, and nothing that varies without cause.

    Attributes
    ----------
    package_version : str
    input_name : str
        File name of the input, for the reader's orientation only. The name and
        not the path: an absolute path records where the person happened to
        keep the file, which differs between two checkouts of the same
        repository and would make the byte equality of D030 unreachable on any
        machine but the one that produced the reference. It also puts the
        person's home directory inside a report they may hand to someone else.
        See D042.
    input_sha256 : str
        The hash is what identifies the data. Two runs over files with the same
        hash are runs over the same data whatever the paths say, which is why
        dropping the directory costs nothing.
    config_sha256 : str
        Hash of the configuration file, for the same reason.
    seed : int
    n_replications : int
    executed_at : str
        ISO 8601 in UTC. **The only field that changes between two runs of the
        same input with the same seed**, which is what makes the byte for byte
        criterion of ``05`` checkable at all.
    """

    package_version: str
    input_name: str
    input_sha256: str
    config_sha256: str
    seed: int
    n_replications: int
    executed_at: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The complete output: provenance, declared parameters, and the evidence panel.

    Attributes
    ----------
    provenance : RunProvenance
    grid : mapping
        ``period``, ``periods_per_year``, ``calendar_id``, ``basis``,
        ``initial_capital`` and ``active_fraction``. Every one of them changes
        the numbers, and ``01`` requires all of them to be present.
    parameters : mapping
        ``risk_free_rate``, the HAC bandwidth actually used, the P&L coherence
        tolerances, the confidence level, the block length, the barrier. Same
        requirement, same reason.
    panel : tuple of Evidence
        In a fixed order, so the serialisation is stable.
    warnings : tuple of str
        Run level warnings, distinct from the per section ones.

    Notes
    -----
    There is deliberately no aggregate score. ``01`` lists a single letter grade
    among the explicit non goals, and ``02`` section 7 says the verdict is never
    presented as a number without the panel. The certainty equivalent ordering
    of v0.9 will sit **next to** this panel, never instead of it.
    """

    provenance: RunProvenance
    grid: dict[str, Any]
    parameters: dict[str, Any]
    panel: tuple[Evidence, ...] = ()
    warnings: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        names = [entry.name for entry in self.panel]
        if len(names) != len(set(names)):
            raise ValueError(f"panel entries must have unique names, got {names}")
        required_grid = {
            "period",
            "periods_per_year",
            "calendar_id",
            "basis",
            "initial_capital",
            "active_fraction",
        }
        missing = sorted(required_grid - set(self.grid))
        if missing:
            raise ValueError(
                f"the report must declare every grid field of 01, missing {missing}; "
                "without them the report is not reproducible and is worth nothing"
            )

    def entry(self, name: str) -> Evidence:
        """Look up one panel entry by name.

        Raises
        ------
        KeyError
            If the section is not in the panel at all. That is different from a
            section present with ``NOT_REQUESTED``, and the difference matters:
            the first is a bug in the pipeline, the second is a declared
            absence.
        """
        for candidate in self.panel:
            if candidate.name == name:
                return candidate
        raise KeyError(
            f"{name!r} is not in the panel; present sections are {[e.name for e in self.panel]}"
        )

    @property
    def sections_run(self) -> tuple[str, ...]:
        """Names of the sections that produced a result."""
        return tuple(entry.name for entry in self.panel if entry.ran)

    @property
    def sections_absent(self) -> dict[str, str]:
        """Names of the sections that did not, mapped to the reason."""
        return {
            entry.name: f"{entry.status}: {entry.reason}" for entry in self.panel if not entry.ran
        }
