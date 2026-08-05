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

## Install

```bash
pip install qvalid
qvalid validate trades.csv --config run.yaml --out report.html
qvalid ui                                   # drag the log in, in a browser
```

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
