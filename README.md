# Quantify

Takes a trade log. Decides whether its numbers survive correction for search, sampling error,
serial dependence and regime. Writes a report that names every test it could not run.

> **The tool is Quantify. The package is `qvalid`.** The name `quantify` was already taken on
> PyPI by an unrelated project when this one was published, so the distribution and the import
> carry the shorter name and the tool carries the one it was always called. See D045 and D058.

```bash
uv run python examples/validate_full.py
```

One command, no network, no configuration. It reads two files from `tests/fixtures` and writes
`examples/output/report.{html,json,tex}`.

## What it decides, and on what basis

A Sharpe ratio computed from a trade log is almost always reported without the three things that
determine whether it means anything: how many configurations were tried before this one, how wide
the sampling interval around it is, and whether the returns are serially dependent. `qvalid`
computes the ratio and all three corrections, and refuses to state a verdict when any of them is
missing.

| Question | Method |
| --- | --- |
| Is the Sharpe distinguishable from zero? | Delta method interval, Mertens (2002), with Newey and West (1987) HAC |
| Does it survive the search that produced it? | Deflated Sharpe and PBO, Bailey and Lopez de Prado (2014, 2017) |
| Is one strategy better than another? | White (2000) Reality Check, Hansen (2005) SPA |
| How bad can the drawdown get? | Stationary bootstrap, Politis and Romano (1994), against Magdon Ismail et al. (2004) |
| What is the chance of ruin? | First passage with the Broadie, Glasserman and Kou (1997) continuity correction |
| Does the edge live in one regime? | Causal labelling, Welch (1951) unequal variance test |
| Would it pass a proprietary desk? | Barrier model over daily paths, rules declared in YAML |

## The example, verbatim

The sample log has 760 trades over 2.91 years. The report says:

```
Sharpe (sqrt q)              -0.91   interval [-2.58, 0.76]
observed max drawdown        0.154   quantile 0.68 of the simulated distribution
probability of ruin          0.211   at a barrier of 85,000
regime equality of means     p = 1.6e-11   over 9 states

deflated_sharpe   NOT_REQUESTED   the number of configurations tested was not declared
verdict           SUPPRESSED      required sections did not run
```

The interval contains zero, so the ratio is not distinguishable from zero. The regime test
finds a real difference. And the verdict is **suppressed**, not negative: the run cannot say how
many configurations were tried, so no correction for search was applied, and a strategy whose
search correction did not run is not comparable with one whose did. Estimating that number would
fabricate the input that determines the answer.

This is the design. An absent test is a typed state in the report, never a missing field and
never a pass. No aggregate grade is produced, because collapsing heterogeneous evidence into one
letter is the defect the tool exists to correct.

## And the other direction

A tool that has only ever been shown to say no is indistinguishable from one that cannot say
anything else. `tests/fixtures/trades_winner.csv` is a log with real positive expectancy and the
twenty configuration sweep that produced it:

```
Sharpe (sqrt q)               1.89   interval [0.75, 3.03]
probability against zero      0.99931
deflated Sharpe               0.87707   against the best of 20 configurations
PBO                           0.208
minimum track record          201 periods required, 760 observed
verdict                       certainty equivalent +0.396
```

The two probabilities are the whole argument in one place. Measured against zero this Sharpe is
all but certain. Measured against the best of the twenty configurations that were tried before
it, it is merely likely. That gap is what a backtest report normally omits, and the gap is not
a rounding difference: it is twelve points of confidence that belong to the search rather than
to the strategy.

## Install

```bash
pip install qvalid
qvalid ui                                   # drag the log in; configure it in the browser
```

Drop a CSV on the page and the form arrives filled in from your own file: each field
pre-selected to the column whose name matched, the cost convention set from the sign of your
cost column, and the timestamp pattern read from the column itself. That last one is worth
a note. `08.03.2022` is the eighth of March and also the third of August, so a single stamp
cannot settle day-first against month-first. Reading the whole column usually can, and when it
cannot, both readings are shown side by side rather than one being chosen.

The multiplier box stays empty. Your file's own arithmetic is printed next to it, and if the two
disagree that disagreement is the finding.

Or the same three steps on the command line:

```bash
qvalid inspect trades.csv                   # a column mapping to start from
qvalid probe trades.csv -m mapping.yaml     # a symbology, with the multiplier your file implies
qvalid validate trades.csv --config run.yaml --out report.html
```

`inspect` reads the header of your export and prints a mapping, marking what it could not
work out rather than choosing:

```yaml
columns:
  entry_ts: Open Time
  entry_px:   # UNRESOLVED, another field also matched Price; decide which gets it
  exit_px:    # UNRESOLVED, another field also matched Price; decide which gets it
```

It prints and never saves. The mapping records which column was read as what, which is what
lets somebody else reproduce a number, so the file has to be one you chose. See D060.

`probe` then reads your numbers and inverts the P&L identity to recover what multiplier each
symbol implies, printing it beside an empty slot rather than into it:

```yaml
symbols:
  ESZ4:
    multiplier:   # implied 50, from 745 trades
#   ESZ4: consistent with pnl_convention: NET (spread 0.0e+00 against 2.3e-02 for the other)
```

That last line is worth a note. Whether a P&L column is net or gross of costs was documented
here as undetectable, because the error leaves a residual of exactly one fee per trade and no
single trade breaks any tolerance. Across trades it is detectable after all: under the wrong
convention the implied multiplier scatters, under the right one it is constant. The limit is
measured rather than assumed. Once costs fall below the rounding of the P&L column the
difference is gone, and `probe` says so instead of answering. See D061.

Developing it rather than using it? Install from the checkout so the `qvalid` command is the
code you are editing, and not a copy from PyPI under the same name:

```bash
pip install -e .
```

Python 3.12 or newer. Importers for generic CSV, TradingView and NinjaTrader; a symbology map
supplies contract multiplier and tick size, which the P&L coherence check needs.

## Reproducibility

Two runs on the same input with the same seed, **in one environment**, produce byte identical
reports with the timestamp excluded. That is the claim about the seed governing everything, and
it is checked exactly.

Across environments the claim is weaker and measured rather than hoped for. Changing the version
of numpy or scipy moves two of the report's 144 values by one or two units in the last place,
because a third moment and an F survival function are reductions whose summation order those
libraries choose. Every run of the test suite compares against the committed reference at `1e-9`
relative, which is a thousand times tighter than the six significant figures the report renders,
so the report a person reads is identical. Text is compared with no tolerance at all: a regime
identifier or a suppression reason that changed is not a rounding difference.

Nothing inside `core` calls the BLAS, because the BLAS splits long reductions across threads and
the summation order would then depend on how many cores the machine has.

## Licence

MIT. Design notes, the mathematical specification and the decision log are in `docs/`,
in Portuguese.
