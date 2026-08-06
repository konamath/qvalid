"""The three configuration files, drafted as text. See D063.

Every line here was previously written inline in :mod:`qvalid.cli` as a
sequence of echo calls. The browser needed the same drafts, and a second copy
of prose that names enum values is a guarantee that the two front ends will
drift: D062 was a comment naming a value that did not exist, found only because
someone walked a real file, and two copies double that surface.

So the drafts live in one place and both front ends render what this returns.
Nothing here decides anything. It formats what :mod:`qvalid.adapters.suggest`
and :mod:`qvalid.adapters.probe` observed, and marks every field those two
cannot observe as a decision the person still owes.

This module sits beside :mod:`qvalid.pipeline` rather than inside ``adapters``
or ``report``: like the pipeline it composes adapters for a caller, and unlike
``report`` it has nothing to do with the validation report.
"""

from __future__ import annotations

from collections.abc import Sequence

from qvalid.adapters.probe import Declarations, Detectability, SymbolProbe
from qvalid.adapters.suggest import suggest_columns
from qvalid.adapters.tradelog import REQUIRED_FIELDS, FeeConvention, PnlConvention

__all__ = [
    "UNREADABLE_FIELDS",
    "evidence_lines",
    "mapping_draft",
    "run_config_draft",
    "symbology_draft",
]

UNREADABLE_FIELDS = ("fee_convention", "pnl_convention", "timestamp_format", "timezone")
"""The four a header cannot show, and all four were wrong in D062's first walk.

They are printed with a value so the draft loads, and marked ``DECIDE`` so the
value does not read as a reading. ``inspect`` refuses to resolve an ambiguous
column and used to state these four as though it had looked them up, which was
the same false confidence in a different place.
"""


def mapping_draft(header: Sequence[str], *, source_name: str) -> str:
    """Draft a column mapping from a header row alone.

    Parameters
    ----------
    header : sequence of str
        The CSV's column names, in file order.
    source_name : str
        The log's filename, for the comment at the top.

    Returns
    -------
    str
        YAML. Loadable as it stands only when every field found a column;
        otherwise the unresolved lines carry no value and the file refuses to
        load, which is the intended outcome under D060.
    """
    found = suggest_columns(header)
    fee = " | ".join(member.value for member in FeeConvention)
    pnl = " | ".join(member.value for member in PnlConvention)
    lines = [
        f"# draft mapping for {source_name}, from its header. Read it before saving.",
        "# Every line is a guess about which column means what. See D016 and D060.",
        "source: generic",
        "pnl_source: COLUMN",
        "# The four below are GUESSES, not readings. The header cannot show any of them,",
        "# and all four change every number in the report. `qvalid probe` checks two.",
        f"fee_convention: {FeeConvention.MAGNITUDE.value}"
        f"   # DECIDE: {fee}. NEGATED when costs arrive with a minus sign",
        f"pnl_convention: {PnlConvention.NET.value}"
        f"         # DECIDE: {pnl}. NET means after costs. See D017",
        'timestamp_format: "%Y-%m-%d %H:%M:%S"'
        "   # DECIDE: must match the export exactly, day first is %d.%m.%Y",
        "timezone: America/New_York  # DECIDE: the zone the export's clock is in",
        "columns:",
    ]
    for name in (*REQUIRED_FIELDS, "pnl"):
        if name in found.columns:
            lines.append(f"  {name}: {found.columns[name]}")
        elif name in found.ambiguous:
            options = found.ambiguous[name]
            reason = (
                f"matched {len(options)} columns, pick one: {', '.join(options)}"
                if len(options) > 1
                else f"another field also matched {options[0]}; decide which gets it"
            )
            lines.append(f"  {name}:   # UNRESOLVED, {reason}")
        else:
            lines.append(f"  {name}:   # NOT FOUND in the header")
    lines.append(f"tag_columns: [{', '.join(found.unused)}]   # columns nothing claimed")
    return "\n".join(lines)


def symbology_draft(probes: Sequence[SymbolProbe], *, source_name: str) -> str:
    """Draft a symbology map, with each implied multiplier beside an empty slot.

    The slot stays empty on purpose. D007 keeps the multiplier a declared
    input, and a value recovered from the same file it will later validate is
    not independent evidence; its use is to disagree with the declaration when
    the declaration is wrong.
    """
    lines = [
        f"# draft symbology for {source_name}. The multipliers are NOT filled in.",
        "# Each `implied` is what your own file's P&L arithmetic works out to.",
        "# Put your contract's real multiplier in the empty slot and compare. See D007.",
        "symbols:",
    ]
    for entry in probes:
        note = (
            f"implied {entry.implied:.6g}, from {entry.n_usable} trades"
            if entry.is_readable
            else (
                f"NOT READABLE: the P&L column is rounded to {entry.pnl_quantum:g}, "
                f"against a typical trade of {entry.typical_pnl:g}"
            )
        )
        lines += [
            f"  {entry.symbol}:",
            f"    multiplier:   # {note}",
            "    tick_size:    # smallest price increment; the file cannot show this",
            "    venue:        # exchange or broker",
            "    currency:",
            "    calendar: WEEKDAYS_UTC",
            f"    contract_root: {entry.symbol}",
            "    source_ids:",
            f"      generic: {entry.symbol}",
        ]
    return "\n".join(lines)


def run_config_draft(*, mapping_path: str, symbology_path: str) -> str:
    """Draft a run configuration.

    Nothing here is recoverable from the trade log, and that is the honest
    shape of it: capital, seed, risk free rate and ruin barrier are the
    person's choices about how to be judged, not facts about their trades.
    """
    return "\n".join(
        [
            "# Run configuration. Nothing below can be read from your trade log:",
            "# every line is a choice about how you want to be judged. See D016.",
            f"mapping_path: {mapping_path}",
            f"symbology_path: {symbology_path}",
            "initial_capital: 100000.0   # the account this record belongs to",
            "basis: FIXED_INITIAL        # or CURRENT_EQUITY to compound. See D023",
            "seed: 20260805              # any integer; it governs every simulation",
            "risk_free_rate: 0.0         # annual, geometric. Printed in the report",
            "n_paths: 2000               # bootstrap replications",
            "ruin_barrier: 85000.0       # the equity at which the account is over",
            "# n_trials: 50              # configurations you tried before this one.",
            "#                           # Without it no correction for search runs",
            "#                           # and the verdict is suppressed. See D004",
        ]
    )


def evidence_lines(
    seen: Declarations, probes: Sequence[SymbolProbe], *, declared_fee: str
) -> list[str]:
    """Report what the data says about the declarations the header could not show.

    Returns plain lines rather than YAML, because this is not a file: it is the
    disagreement between what the person wrote and what their file contains.
    """
    lines = ["What your mapping declares, against what the file shows:"]
    for entry in probes:
        if entry.detectability is Detectability.DECISIVE:
            lines.append(
                f"  {entry.symbol}: consistent with pnl_convention: {entry.convention} "
                f"(spread {min(entry.spread_net, entry.spread_gross):.1e} against "
                f"{max(entry.spread_net, entry.spread_gross):.1e} for the other)"
            )
        elif entry.detectability is Detectability.NO_COST:
            lines.append(f"  {entry.symbol}: every fee is zero, so NET and GROSS coincide here.")
        else:
            lines.append(
                f"  {entry.symbol}: costs are smaller than the rounding of the P&L column, "
                "so the file cannot say. You must know. See D017."
            )
    if seen.fee_convention_implied is None:
        lines.append(f"  fees are {seen.fee_sign}, which implies no convention either way.")
    else:
        agrees = seen.fee_convention_implied == declared_fee
        lines.append(
            f"  fees are {seen.fee_sign} in the file, implying "
            f"fee_convention: {seen.fee_convention_implied}. "
            + (
                "Agrees with your mapping."
                if agrees
                else f"YOUR MAPPING SAYS {declared_fee}, WHICH DISAGREES."
            )
        )
    lines.append(f"  first entry stamp reads {seen.sample_entry_ts!r}")
    lines.append(f"  first exit stamp reads  {seen.sample_exit_ts!r}")
    if seen.timestamp_format_parses is False:
        lines.append(
            "  YOUR timestamp_format DOES NOT PARSE THAT. "
            "A day first export needs %d.%m.%Y or %d/%m/%Y."
        )
    return lines
