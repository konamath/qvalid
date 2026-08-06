"""The configuration form, and the one place the three files are assembled. See D066.

Until now the browser showed three boxes of YAML. That removed the need to find
a template and left everything else: the person still had to read comments to
learn which words were legal, still had to know ``strftime``, and still had to
type column names they could not see. A text box is not an interface, it is a
file with a different background colour.

This renders real controls, and every default in it is either something read
from the person's own file or something marked as a choice they still owe.

**One authority for the YAML.** :func:`build_files` assembles the three
documents from the submitted fields, in Python, on the server. The inline
script does no assembly: it highlights two fields claiming one column, toggles
the free text boxes, and gates the submit button. A script that also built YAML
would be a second implementation of the configuration, and D063 exists because
a second copy of exactly this kind of thing had already gone wrong once.

D016 is unchanged. The files are still the provenance, so the result page shows
all three in full for the person to keep; without them the run is not
reproducible, which is the whole point of the file being a file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape

from qvalid.adapters.probe import Declarations, SymbolProbe
from qvalid.adapters.suggest import Suggestion
from qvalid.adapters.timeformats import CANDIDATE_FORMATS, FormatMatch
from qvalid.adapters.tradelog import REQUIRED_FIELDS, FeeConvention, PnlConvention

__all__ = ["CURRENCIES", "RUN_FIELDS", "TIMEZONES", "build_files", "render_form"]

MAPPED_FIELDS = (*REQUIRED_FIELDS, "pnl")

FIELD_HINTS = {
    "trade_id": "unique per trade",
    "symbol": "the instrument, as your platform names it",
    "side": "long or short",
    "qty": "contracts, lots or shares",
    "entry_ts": "when the position opened",
    "exit_ts": "when it closed; this is the one that dates the P&L",
    "entry_px": "price in, before the multiplier",
    "exit_px": "price out",
    "fees": "commission and any per trade cost",
    "pnl": "the profit column as your platform reports it",
}

TIMEZONES = (
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Australia/Sydney",
)
"""Offered because most exports come from one of these, never inferred.

A naive stamp carries no zone, and guessing one shifts every trade that sits
near a session boundary into the wrong period. The form asks; it does not
default to the machine's own zone, which would make the same file produce
different reports on two laptops.
"""

CURRENCIES = ("USD", "EUR", "GBP", "BRL", "JPY", "CHF", "AUD", "CAD", "MXN", "HKD")
"""Offered rather than typed, because the schema takes only a three letter code.

The first version of this form asked for venue and currency together as "free
text, for the record", with neither marked required, and wrote ``UNSPECIFIED``
into both when they were left blank. Venue tolerates that; currency does not,
and the whole run was refused at the last step with a pydantic error about a
value the person never typed. Currency is not decoration: the absolute
tolerance of the coherence identity is one tick **in account currency**. See
D069.
"""

RUN_FIELDS: tuple[tuple[str, str, str, str, bool], ...] = (
    ("initial_capital", "Initial capital", "100000", "The account this record belongs to", True),
    ("seed", "Seed", "20260805", "Any integer. It governs every simulation in the run", True),
    (
        "risk_free_rate",
        "Risk free rate",
        "0.0",
        "Annual, as a decimal. Converted geometrically and printed in the report",
        True,
    ),
    ("n_paths", "Bootstrap paths", "2000", "More is slower and smoother", True),
    (
        "ruin_barrier",
        "Ruin barrier",
        "85000",
        "The equity at which the account is over. Clear it to skip the ruin section",
        False,
    ),
    (
        "n_trials",
        "Configurations tried",
        "",
        "How many variants you tested before this one. Leave blank and no correction "
        "for search runs, and the verdict is suppressed rather than guessed. See D004",
        False,
    ),
)
"""Name, label, the value the form offers, the hint, and whether blank is allowed.

Nothing here is readable from a trade log, which is why it is all typed. Capital,
seed, rate and barrier are choices about how the person wants to be judged, and
``n_trials`` decides whether the report can reach a verdict at all. It is left
empty deliberately: a default would fabricate the input that determines the
answer.

**The offered value is not a fallback.** It fills the box on the way out and has
no standing on the way back: a required field that returns empty is refused by
:func:`build_files`, never quietly replaced. The two were the same thing until
D067, and the consequence was that a number the browser declined to accept, a
decimal comma on a machine whose locale uses one, produced a report about a
hundred thousand when the person had typed two hundred and fifty. Every one of
these changes every figure in the report, and a substitution nobody sees is the
exact failure this project exists to remove.
"""

_SCRIPT = """
(function () {
  var form = document.getElementById('config');
  if (!form) return;
  var picks = Array.prototype.slice.call(form.querySelectorAll('select[data-column]'));
  var button = form.querySelector('button[type=submit]');
  var warn = document.getElementById('collisions');

  function toggleCustom(name) {
    var select = form.querySelector('[name="' + name + '"]');
    var box = form.querySelector('[name="' + name + '_custom"]');
    if (!select || !box) return;
    function sync() { box.hidden = select.value !== '__custom__'; }
    select.addEventListener('change', sync); sync();
  }
  toggleCustom('timestamp_format'); toggleCustom('timezone');

  function check() {
    var seen = {}, clashes = {}, blank = false;
    picks.forEach(function (s) {
      if (!s.value) { blank = true; return; }
      if (seen[s.value]) clashes[s.value] = true;
      seen[s.value] = true;
    });
    picks.forEach(function (s) {
      s.classList.toggle('clash', !!clashes[s.value]);
    });
    var names = Object.keys(clashes);
    if (names.length) {
      warn.hidden = false;
      warn.textContent = 'Two fields claim ' + names.join(', ') +
        '. One column cannot mean two things, and the report would not say so.';
    } else if (blank) {
      warn.hidden = false;
      warn.textContent = 'Every field needs a column before this can run.';
    } else {
      warn.hidden = true;
    }
    button.disabled = names.length > 0 || blank;
  }
  picks.forEach(function (s) { s.addEventListener('change', check); });
  check();
})();
"""
"""Interactivity only. It highlights, toggles and gates; it assembles nothing."""


def _select(name: str, options: Sequence[tuple[str, str]], chosen: str, extra: str = "") -> str:
    body = "".join(
        f'<option value="{escape(value)}"{" selected" if value == chosen else ""}>'
        f"{escape(label)}</option>"
        for value, label in options
    )
    return f'<select name="{escape(name)}" {extra}>{body}</select>'


def _preview(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Show the person's own first rows, so the column names are not abstractions."""
    head = "".join(f"<th>{escape(name)}</th>" for name in header)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<div class='scroll'><table class='preview'><tr>{head}</tr>{body}</table></div>"


def render_form(
    *,
    token: str,
    log_name: str,
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    suggestion: Suggestion,
    declarations: Declarations | None,
    stamps: FormatMatch | None,
    probes: Sequence[SymbolProbe],
    submitted: Mapping[str, str] | None = None,
) -> str:
    """Render the configuration form, pre filled from the file itself.

    Parameters
    ----------
    token : str
        Names the stored upload for the second request.
    header, rows : sequence
        The person's column names and first rows, shown so the choices below
        are made against something visible.
    suggestion : Suggestion
        Column matches from the header, per D060.
    declarations : Declarations or None
        Fee sign and sample stamps, per D062. ``None`` when the draft mapping
        was not usable enough to read them.
    stamps : FormatMatch or None
        Patterns that read the whole timestamp column, per D066.
    probes : sequence of SymbolProbe
        One per symbol, carrying the multiplier the file's own arithmetic
        implies. Shown beside an empty box, never inside it, per D007.
    submitted : mapping, optional
        What was sent last time, so a refusal does not cost the person their
        answers.
    """
    prior = dict(submitted or {})
    columns = [("", "choose a column"), *((name, name) for name in header)]

    rows_html = ""
    for field in MAPPED_FIELDS:
        chosen = prior.get(f"col__{field}", suggestion.columns.get(field, ""))
        note = ""
        if field in suggestion.ambiguous:
            note = " <em>two fields matched this; you decide</em>"
        elif field not in suggestion.columns:
            note = " <em>no column matched by name</em>"
        rows_html += (
            f"<tr><th>{escape(field)}</th><td>"
            + _select(f"col__{field}", columns, chosen, 'data-column="1"')
            + f"</td><td class='hint'>{escape(FIELD_HINTS[field])}{note}</td></tr>"
        )

    claimed = {prior.get(f"col__{f}", suggestion.columns.get(f, "")) for f in MAPPED_FIELDS}
    tags = "".join(
        f'<label class="inline"><input type="checkbox" name="tag__{escape(name)}"'
        f"{' checked' if name in suggestion.unused else ''}> {escape(name)}</label>"
        for name in header
        if name not in claimed
    )

    fee_default = (declarations.fee_convention_implied if declarations else None) or "MAGNITUDE"
    fee_note = ""
    if declarations and declarations.fee_convention_implied:
        fee_note = (
            f"your cost column is {declarations.fee_sign.lower()}, which means "
            f"{declarations.fee_convention_implied}"
        )
    elif declarations:
        fee_note = f"your cost column is {declarations.fee_sign.lower()}; it implies neither"

    pnl_note = "the file cannot settle this on its own"
    for entry in probes:
        if entry.convention is not None:
            pnl_note = (
                f"{entry.symbol}: the arithmetic is consistent with {entry.convention}, "
                "because the other reading scatters the multiplier"
            )
            break

    fmt_options = [(pattern, pattern) for pattern in CANDIDATE_FORMATS]
    fmt_default = CANDIDATE_FORMATS[0]
    fmt_note = "not read from the file"
    if stamps is not None and stamps.parsing:
        fmt_options = [
            (p, p + (" (reads your column)" if p in stamps.parsing else ""))
            for p in CANDIDATE_FORMATS
        ]
        fmt_default = stamps.parsing[0]
        if stamps.ambiguous:
            fmt_note = f"AMBIGUOUS. {stamps.disagreement}. Only you know which"
        else:
            fmt_note = f"read from every row of {escape(log_name)}, and only this one fits"
    fmt_options.append(("__custom__", "something else"))

    symbols = ""
    for entry in probes:
        implied = (
            f"your file's arithmetic implies {entry.implied:.6g}"
            if entry.is_readable
            else "your P&L column is rounded too coarsely to imply one"
        )
        symbols += (
            f"<fieldset><legend>{escape(entry.symbol)}</legend>"
            f"<label>Multiplier<small>{escape(implied)}. Put in your contract's real one; "
            "if they disagree, that disagreement is the finding</small>"
            f'<input type="number" step="any" name="sym__{escape(entry.symbol)}__multiplier" '
            f'value="{escape(prior.get(f"sym__{entry.symbol}__multiplier", ""))}" required></label>'
            "<label>Tick size<small>smallest price increment; the file cannot show this</small>"
            f'<input type="number" step="any" name="sym__{escape(entry.symbol)}__tick_size" '
            f'value="{escape(prior.get(f"sym__{entry.symbol}__tick_size", ""))}" required></label>'
            "<label>Currency<small>three letter code, the one the P&amp;L above is "
            "denominated in. Required: the tolerance of the coherence check is one tick "
            "in account currency, so it is not decoration</small>"
            + _select(
                f"sym__{entry.symbol}__currency",
                [("", "choose"), *((code, code) for code in CURRENCIES)],
                prior.get(f"sym__{entry.symbol}__currency", ""),
            )
            + "</label><label>Venue <em>optional</em><small>exchange or broker, kept for the "
            "record and used in no calculation</small>"
            f'<input type="text" name="sym__{escape(entry.symbol)}__venue" placeholder="CME" '
            f'value="{escape(prior.get(f"sym__{entry.symbol}__venue", ""))}"></label>'
            "</fieldset>"
        )
    if not symbols:
        symbols = (
            "<p class='hint'>No symbol could be read yet, because the columns above are not "
            "settled. Choose them and submit; this section fills itself in.</p>"
        )

    run = ""
    for name, label, offered, hint, required in RUN_FIELDS:
        mark = "" if required else " <em>optional</em>"
        run += (
            f"<label>{escape(label)}{mark}<small>{escape(hint)}</small>"
            f'<input type="number" step="any" name="{escape(name)}" '
            f'value="{escape(prior.get(name, offered))}"></label>'
        )

    return (
        f'<form method="post" action="/finish" id="config">'
        f'<input type="hidden" name="token" value="{escape(token)}">'
        f"<h2>Your file</h2><p class='hint'>First rows of "
        f"<code>{escape(log_name)}</code>, so the names below mean something.</p>"
        + _preview(header, rows)
        + "<h2>Which column is which</h2>"
        "<p id='collisions' class='error' hidden></p>"
        f"<table class='fields'>{rows_html}</table>"
        f"<h2>Labels to keep</h2><p class='hint'>Columns nothing claimed. Kept alongside "
        f"each trade, never used in a calculation.</p><p>{tags or '<em>none left over</em>'}</p>"
        "<h2>Conventions</h2>"
        "<label>Cost column"
        f"<small>{escape(fee_note)}</small>"
        + _select(
            "fee_convention",
            [
                (
                    m.value,
                    m.value
                    + (
                        " (positive costs)"
                        if m is FeeConvention.MAGNITUDE
                        else " (costs arrive negative)"
                    ),
                )
                for m in FeeConvention
            ],
            prior.get("fee_convention", fee_default),
        )
        + "</label><label>P&amp;L column"
        f"<small>{escape(pnl_note)}</small>"
        + _select(
            "pnl_convention",
            [
                (
                    m.value,
                    m.value + (" (after costs)" if m is PnlConvention.NET else " (before costs)"),
                )
                for m in PnlConvention
            ],
            prior.get("pnl_convention", "NET"),
        )
        + "</label><label>Timestamp format"
        f"<small>{fmt_note}</small>"
        + _select("timestamp_format", fmt_options, prior.get("timestamp_format", fmt_default))
        + '<input type="text" name="timestamp_format_custom" placeholder="%Y-%m-%d %H:%M:%S" '
        f'value="{escape(prior.get("timestamp_format_custom", ""))}" hidden>'
        "</label><label>Time zone"
        "<small>the zone your export's clock is in. Never guessed: the same file must not "
        "produce two reports on two machines</small>"
        + _select(
            "timezone",
            [*((z, z) for z in TIMEZONES), ("__custom__", "something else")],
            prior.get("timezone", "UTC"),
        )
        + '<input type="text" name="timezone_custom" placeholder="Region/City" '
        f'value="{escape(prior.get("timezone_custom", ""))}" hidden>'
        "</label>"
        f"<h2>Contract</h2>{symbols}"
        f"<h2>How you want to be judged</h2><p class='hint'>None of this is in your trade log."
        "</p>{}".format(run)
        + "<button type=submit>Validate</button></form>"
        f"<script>{_SCRIPT}</script>"
    )


def _chosen(fields: Mapping[str, str], name: str) -> str:
    """Read a select, falling back to its free text box when ``something else`` was picked."""
    value = fields.get(name, "").strip()
    return fields.get(f"{name}_custom", "").strip() if value == "__custom__" else value


def build_files(fields: Mapping[str, str]) -> tuple[str, str, str]:
    """Assemble the three YAML documents from the submitted form.

    The single authority. Both the file written to disk and the copy shown to
    the person come from here, so there is no arrangement in which the report
    was produced by a configuration different from the one displayed.

    Returns
    -------
    tuple of str
        Mapping, symbology and run configuration, in that order.

    Raises
    ------
    ValueError
        A required answer is missing or two fields claim one column. Raised
        rather than defaulted: every one of these changes a number, and the
        interface refusing is cheaper than a report that is quietly about
        something else.
    """
    columns = {field: fields.get(f"col__{field}", "").strip() for field in MAPPED_FIELDS}
    blank = [field for field, column in columns.items() if not column]
    if blank:
        raise ValueError(f"no column chosen for {', '.join(blank)}")
    taken: dict[str, str] = {}
    for field, column in columns.items():
        if column in taken:
            raise ValueError(
                f"{taken[column]} and {field} both claim {column!r}; one column cannot mean two "
                "things, and the report would not tell you which it used"
            )
        taken[column] = field

    stamp_format = _chosen(fields, "timestamp_format")
    zone = _chosen(fields, "timezone")
    if not stamp_format:
        raise ValueError("no timestamp format given")
    if not zone:
        raise ValueError("no time zone given")

    tags = sorted(name[len("tag__") :] for name in fields if name.startswith("tag__"))
    mapping = "\n".join(
        [
            "# Written from the browser form. This file is the provenance of your run:",
            "# it records which column was read as what. Keep it. See D016 and D066.",
            "source: generic",
            "pnl_source: COLUMN",
            f"fee_convention: {fields.get('fee_convention', 'MAGNITUDE').strip()}",
            f"pnl_convention: {fields.get('pnl_convention', 'NET').strip()}",
            f'timestamp_format: "{stamp_format}"',
            f"timezone: {zone}",
            "columns:",
            *(f"  {field}: {column}" for field, column in columns.items()),
            f"tag_columns: [{', '.join(tags)}]",
        ]
    )

    names = sorted({key.split("__")[1] for key in fields if key.startswith("sym__")})
    if not names:
        raise ValueError("no symbol was configured")
    lines = ["symbols:"]
    for symbol in names:
        multiplier = fields.get(f"sym__{symbol}__multiplier", "").strip()
        tick = fields.get(f"sym__{symbol}__tick_size", "").strip()
        currency = fields.get(f"sym__{symbol}__currency", "").strip().upper()
        if not multiplier or not tick:
            raise ValueError(f"{symbol} needs both a multiplier and a tick size")
        if len(currency) != 3 or not currency.isalpha():
            # Refused here rather than at the last step. Writing a placeholder
            # and letting the schema reject it produced a pydantic error naming
            # a value the person had never typed. See D069.
            raise ValueError(
                f"{symbol} needs a three letter currency code, got {currency or 'nothing'}"
            )
        lines += [
            f"  {symbol}:",
            f"    multiplier: {multiplier}",
            f"    tick_size: {tick}",
            f"    venue: {fields.get(f'sym__{symbol}__venue', '').strip() or 'UNSPECIFIED'}",
            f"    currency: {currency}",
            "    calendar: WEEKDAYS_UTC",
            f"    contract_root: {symbol}",
            "    source_ids:",
            f"      generic: {symbol}",
        ]
    symbology = "\n".join(lines)

    run = [
        "# Every parameter that changes a number. Keep this with the other two.",
        "mapping_path: mapping.yaml",
        "symbology_path: symbology.yaml",
        "basis: FIXED_INITIAL",
    ]
    for name, label, _, _, required in RUN_FIELDS:
        value = fields.get(name, "").strip()
        if not value:
            if required:
                raise ValueError(
                    f"{label} came back empty. It is not defaulted: a value that changes every "
                    "figure in the report has to be one you chose. If you typed a decimal comma, "
                    "your browser rejected it and sent nothing; use a point"
                )
            continue
        run.append(f"{name}: {value}")
    return mapping, symbology, "\n".join(run)
