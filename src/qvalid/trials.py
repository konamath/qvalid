"""Build the matrix of every configuration tested, from the logs you already have. See D072.

The deflated Sharpe of ``02`` section 3 needs the dispersion **across** the trial
Sharpe ratios, so a declared count is not enough and D004 refuses to estimate the
rest. Until now that matrix had to be produced by hand, which meant the section
that separates this project from a spreadsheet of metrics was reachable only by
someone who had already done the hard part somewhere else.

Anyone who swept twenty parameter values has twenty trade logs. This turns those
into the artefact the deflation wants, and it is the whole point: the input that
determines whether a Sharpe survives its own search should come from the search,
not from a number typed into a form.

One grid, declared once
-----------------------
D024 made "same grid for every configuration" structural rather than checked,
because comparing Sharpe ratios across grids is an error of unit. So the period
is chosen **once**, on the reference log, and forced on every other. The choice
and its reason are printed, because a grid selected silently is a parameter
nobody declared.

Spans are intersected, never filled
-----------------------------------
Variants of one strategy rarely start and stop on the same day. Inside a
variant's own span an empty period is a zero return, which is D011's convention
and correct: capital was allocated and nothing was traded. **Outside** it, a
period is not a zero, it is an absence, and filling it with zero would tell the
deflation that a configuration sat flat during months it was never run. So the
common window is the intersection, the trimming is reported, and an intersection
too short to carry a Sharpe is refused rather than returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from qvalid.adapters.calendars import weekdays_utc
from qvalid.adapters.symbology import load_symbology
from qvalid.adapters.tradelog import load_mapping, read_trade_log_csv
from qvalid.contracts import FloatArray, IntArray, Period
from qvalid.core.constants import MIN_PERIODS
from qvalid.core.gridding import period_returns, select_grid
from qvalid.exceptions import SchemaError
from qvalid.pipeline import load_config

__all__ = ["TrialBuild", "build_trials"]

_CALENDAR_PADDING_DAYS = 5
"""Padding on each side, so the sentinel calendar covers every log. Mirrors the pipeline."""


@dataclass(frozen=True, slots=True)
class TrialBuild:
    """The matrix and everything about it that a reader would need to check.

    Attributes
    ----------
    names : tuple of str
        Column identifiers, in the order the logs were given. The first is the
        reference whose grid was adopted.
    period_end : tuple of int
        Period close in UTC nanoseconds, one per row of the matrix.
    values : ndarray
        ``(n_periods, n_configs)`` of per period returns on the shared grid.
    period : Period
        The grid every column lives on, chosen once. See D024.
    trimmed : dict of str to tuple of int
        Per configuration, how many periods were dropped from the start and the
        end to reach the common window. Reported rather than hidden, because a
        configuration that lost a third of its history is a different object
        from one that lost two days.
    """

    names: tuple[str, ...]
    period_end: tuple[int, ...]
    values: FloatArray
    period: Period
    trimmed: dict[str, tuple[int, int]]

    @property
    def n_periods(self) -> int:
        """Rows in the matrix, which is the length of the common window."""
        return len(self.period_end)

    @property
    def worst_trim(self) -> int:
        """Most periods any single configuration lost. Zero when spans agreed."""
        return max((start + end for start, end in self.trimmed.values()), default=0)

    def to_csv(self) -> str:
        """Serialise in the format :func:`~qvalid.pipeline._load_trials` reads.

        Header row, first column an ISO 8601 timezone aware period close, one
        further column per configuration. Written here rather than by pandas so
        the column order is the one declared above and not one a library chose.
        """
        lines = ["period_end," + ",".join(self.names)]
        for row, stamp in enumerate(self.period_end):
            moment = datetime.fromtimestamp(stamp / 1e9, tz=UTC)
            cells = ",".join(repr(float(value)) for value in self.values[row])
            lines.append(f"{moment.isoformat()},{cells}")
        return "\n".join(lines) + "\n"


def build_trials(
    logs: Sequence[str | Path],
    config_path: str | Path,
    *,
    names: Sequence[str] | None = None,
) -> TrialBuild:
    """Project every variant's log onto one grid and return the aligned matrix.

    Parameters
    ----------
    logs : sequence of path
        One trade log per configuration tested. The **first is the reference**:
        its grid is selected by the ladder of ``02`` section 1.1 and forced on
        the rest, so that the precondition of D024 holds by construction rather
        than by checking afterwards.
    config_path : path
        The run configuration, for the mapping, the symbology, the basis and the
        initial capital. The same file the validation will use, so the trial
        Sharpe ratios are the same quantity as the one being deflated.
    names : sequence of str, optional
        Column identifiers. Defaults to each log's filename without suffix.

    Returns
    -------
    TrialBuild

    Raises
    ------
    SchemaError
        Fewer than two logs, since a deflation against one configuration is not
        a deflation. Duplicate names. A common window shorter than
        :data:`~qvalid.core.constants.MIN_PERIODS`, which is refused with the
        numbers rather than returned as a matrix nobody could use.
    """
    paths = [Path(item) for item in logs]
    if len(paths) < 2:
        raise SchemaError(
            f"a trial matrix needs at least two configurations, got {len(paths)}; "
            "deflating a Sharpe against itself measures nothing"
        )
    labels = tuple(names) if names is not None else tuple(path.stem for path in paths)
    if len(set(labels)) != len(labels):
        raise SchemaError(f"configuration names must be distinct, got {labels}")
    if len(labels) != len(paths):
        raise SchemaError(f"{len(labels)} names for {len(paths)} logs")

    config = load_config(Path(config_path))
    base = Path(config_path).resolve().parent
    symbology = load_symbology(base / config.symbology_path)
    mapping = load_mapping(base / config.mapping_path)
    imported = [read_trade_log_csv(path, mapping, symbology) for path in paths]

    exits = [np.asarray(item.log.exit_ns) for item in imported]
    first = min(int(column.min()) for column in exits)
    last = max(int(column.max()) for column in exits)
    padding = timedelta(days=_CALENDAR_PADDING_DAYS)
    calendar = weekdays_utc(
        datetime.fromtimestamp(first / 1e9, tz=UTC) - padding,
        datetime.fromtimestamp(last / 1e9, tz=UTC) + padding,
    )

    reference = select_grid(
        imported[0].log,
        calendar,
        basis=config.basis,
        initial_capital=config.initial_capital,
        forced_period=config.forced_period,
    ).returns
    period = reference.period

    series = [
        period_returns(
            item.log,
            calendar,
            period=period,
            basis=config.basis,
            initial_capital=config.initial_capital,
        )
        for item in imported
    ]

    # The intersection, not the union. A period outside a variant's own span is
    # an absence and not a zero, and filling it would tell the deflation that a
    # configuration sat flat during months it was never run.
    stamps = [np.asarray(item.period_end_ns) for item in series]
    start = max(int(column[0]) for column in stamps)
    end = min(int(column[-1]) for column in stamps)
    if end <= start:
        raise SchemaError(
            "the configurations share no common period; their windows are "
            + ", ".join(
                f"{name} {datetime.fromtimestamp(int(column[0]) / 1e9, tz=UTC):%Y-%m-%d} to "
                f"{datetime.fromtimestamp(int(column[-1]) / 1e9, tz=UTC):%Y-%m-%d}"
                for name, column in zip(labels, stamps, strict=True)
            )
        )

    columns: list[FloatArray] = []
    trimmed: dict[str, tuple[int, int]] = {}
    common: IntArray | None = None
    for name, item, column in zip(labels, series, stamps, strict=True):
        keep = (column >= start) & (column <= end)
        kept = column[keep]
        if common is None:
            common = kept
        elif not np.array_equal(common, kept):
            raise SchemaError(
                f"{name} does not share the reference's period stamps inside the common window; "
                "the grids agree in period but not in calendar, which D024 forbids"
            )
        columns.append(np.asarray(item.values)[keep])
        trimmed[name] = (int(np.sum(column < start)), int(np.sum(column > end)))

    assert common is not None
    if common.size < MIN_PERIODS:
        raise SchemaError(
            f"the common window holds {common.size} periods, below MIN_PERIODS={MIN_PERIODS}; "
            f"the configurations overlap too little to be compared "
            f"(worst trim {max(a + b for a, b in trimmed.values())} periods)"
        )

    return TrialBuild(
        names=labels,
        period_end=tuple(int(value) for value in common),
        values=np.column_stack(columns),
        period=period,
        trimmed=trimmed,
    )
