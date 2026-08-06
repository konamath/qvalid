"""Guess which column is which, and refuse to be sure. See D060.

Before this, using the tool on a real export meant writing a column mapping by
hand from a template, which is the wall between the tool and its first real
user. This reads the header and proposes a mapping.

**A proposal, never a decision.** D016 makes the mapping versioned provenance:
it records which column was read as what, and that record is what lets someone
else reproduce a number. A mapping written silently by a guesser would be
provenance nobody chose. So this returns a suggestion with its reasons, the
person reads it, and the file they save is theirs.

The refusals are the useful part. A field with no plausible column, and two
fields whose best match is the same column, are both reported rather than
resolved: picking one would produce a mapping that parses and means something
different from what the person has. That is the failure mode this project
exists to remove, and a column guesser is a very easy place to reintroduce it.

Nothing here inspects the data, only the header. Reading values to infer types
would let a column of round numbers pass for a price and a column of dates pass
for anything, and the person would never see the inference that produced their
Sharpe.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from qvalid.adapters.tradelog import REQUIRED_FIELDS

__all__ = ["Suggestion", "suggest_columns"]

ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "trade_id": ("id", "tradeid", "ticket", "orderid", "dealid", "reference", "number"),
    "symbol": ("symbol", "instrument", "ticker", "market", "contract", "asset", "pair"),
    "side": ("side", "direction", "type", "action", "buysell", "longshort", "position"),
    "qty": ("qty", "quantity", "size", "volume", "lots", "contracts", "shares", "units"),
    "entry_ts": (
        "entrytime",
        "opentime",
        "openedat",
        "entrydate",
        "opendate",
        "dateopen",
        "timeopen",
        "entry",
        "opened",
    ),
    "exit_ts": (
        "exittime",
        "closetime",
        "closedat",
        "exitdate",
        "closedate",
        "dateclose",
        "timeclose",
        "exit",
        "closed",
    ),
    "entry_px": ("entryprice", "openprice", "priceopen", "entryrate", "openrate", "avgentry"),
    "exit_px": ("exitprice", "closeprice", "priceclose", "exitrate", "closerate", "avgexit"),
    "fees": ("commission", "fee", "fees", "cost", "costs", "charges", "brokerage", "swap"),
    "pnl": (
        "pnl",
        "pl",
        "profit",
        "profitloss",
        "netpnl",
        "grosspnl",
        "result",
        "realized",
        "realised",
        "netprofit",
    ),
}
"""Column names each field is known by, normalised. Order is preference order.

Compiled from the exports this project has seen and from the vocabularies
``03`` names for TradingView and NinjaTrader. It is not exhaustive and cannot
be: the point of the mapping file is that a source nobody anticipated is still
usable by writing two lines.
"""


_SHORTEST_ABBREVIATION: Final = 3
"""Below this, a column that truncates an alias is a coincidence, not evidence.

Prefix matching runs in two directions. A column longer than the alias is a more
specific name for it, ``entry_time_utc`` for ``entry_time``, and that direction
is safe. The other direction, a column that is a truncation of the alias, is
where a short name matches everything: ``entry_time`` starts with ``e``, so a
column called ``e`` would be read as the entry timestamp with no hesitation.

Measured on three headers, counting fields given the wrong column or none.
Ungated: 1 wrong, a mute ``e`` beating a real ``EntryStamp``. At four
characters: 2 wrong, because ``Sym`` and ``Ref`` are abbreviations people
actually write and the gate rejects them. At three: 0. Three is also the
shortest abbreviation observed in a real header.
"""


def _normalise(name: str) -> str:
    """Strip everything that separates words, so ``Open Time`` meets ``open_time``."""
    return "".join(character for character in name.lower() if character.isalnum())


@dataclass(frozen=True, slots=True)
class Suggestion:
    """What the header appears to offer, and what it does not.

    Attributes
    ----------
    columns : dict of str to str
        Field to column, for the fields matched unambiguously.
    missing : tuple of str
        Fields no column plausibly matched. The person supplies these or
        decides the export cannot be used.
    ambiguous : dict of str to tuple of str
        Fields that matched more than one column, or whose single match another
        field also wants. Reported rather than resolved: a mapping that parses
        and means the wrong thing is worse than one that refuses to be written.

        Contested columns leave **every** claimant unresolved, not all but the
        first. An earlier draft assigned in the order of
        :data:`~qvalid.adapters.tradelog.REQUIRED_FIELDS`, so a lone ``Price``
        went to ``entry_px`` with no hesitation and only ``exit_px`` was
        flagged, which is the arbitrary choice this module exists not to make.
    unused : tuple of str
        Columns no field claimed at all, offered as candidates for
        ``tag_columns``. A column two fields fought over is not here: it is
        spoken for, and listing it as a free label would invite someone to
        resolve the collision by deleting the evidence of it.
    """

    columns: dict[str, str] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    ambiguous: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unused: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """True when every required field found one column and no two collided."""
        return not self.missing and not self.ambiguous


def suggest_columns(header: Sequence[str]) -> Suggestion:
    """Propose a column mapping from a header row.

    Parameters
    ----------
    header : sequence of str
        The CSV's column names, in file order.

    Returns
    -------
    Suggestion
        Never a mapping on its own: what matched, what did not, and what
        collided, so the person can see the guess before adopting it.

    Notes
    -----
    Matching is exact on the normalised name, then by prefix, and never fuzzy.
    An edit distance would match ``exit_price`` to ``entry_price`` at a
    distance of three and put the wrong number in the P&L identity, and the
    coherence check would then blame the multiplier. A field this ambiguous is
    reported, not resolved.

    One precedence rule breaks ties: a field whose name matched a column
    exactly keeps it against fields that only reached it by prefix. That is
    evidence over inference, and it is the sole asymmetry here. Two exact
    claims, or two prefix claims, leave both fields unresolved.
    """
    wanted = (*REQUIRED_FIELDS, "pnl")
    normalised = {_normalise(column): column for column in header}

    claims: dict[str, tuple[tuple[str, ...], bool]] = {}
    for name in wanted:
        for alias in ALIASES.get(name, ()):
            if alias in normalised:
                claims[name] = ((normalised[alias],), True)
                break
        else:
            for alias in ALIASES.get(name, ()):
                hits = tuple(
                    original
                    for key, original in normalised.items()
                    if key.startswith(alias)
                    or (len(key) >= _SHORTEST_ABBREVIATION and alias.startswith(key))
                )
                if hits:
                    claims[name] = (hits, False)
                    break

    rivals: dict[str, list[str]] = {}
    for name, (options, _) in claims.items():
        for column in options:
            rivals.setdefault(column, []).append(name)

    columns: dict[str, str] = {}
    ambiguous: dict[str, tuple[str, ...]] = {}
    for name in wanted:
        options, exact = claims.get(name, ((), False))
        if not options:
            continue
        if len(options) > 1:
            ambiguous[name] = options
            continue
        column = options[0]
        contested = [other for other in rivals[column] if other != name]
        if contested and not (exact and all(not claims[other][1] for other in contested)):
            ambiguous[name] = (column,)
            continue
        columns[name] = column

    return Suggestion(
        columns=columns,
        missing=tuple(name for name in wanted if name not in columns and name not in ambiguous),
        ambiguous=ambiguous,
        unused=tuple(column for column in header if column not in rivals),
    )
