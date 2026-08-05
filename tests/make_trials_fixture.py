"""Build a trial matrix by running a real parameter sweep, D052.

The point of a trial matrix is that it carries the **search**, not one result.
Writing a fixture by drawing fifty independent noise columns would miss what
makes the overfitting tests necessary: in a real sweep the columns are
correlated, because every configuration trades the same underlying series, and
the winner is picked after seeing all of them.

So this sweeps a moving average crossover over the reference series, one column
per window length, and keeps every configuration including the losers. The
selection effect is then real rather than assumed, which is what the deflated
Sharpe and PBO exist to undo.

Run deliberately::

    python tests/make_trials_fixture.py

Substituting a real price series for the reference series changes nothing here
beyond the file it reads.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
WINDOWS = tuple(range(3, 63, 3))
"""Twenty configurations. Enough for the logit ceiling of log(20) to be legible."""


def crossover_returns(series: np.ndarray, window: int) -> np.ndarray:
    """Long when the series is above its own trailing mean, flat otherwise.

    The signal uses data up to and including ``t - 1`` and is applied to the
    return of ``t``, so there is no look ahead. The warm up is flat rather than
    dropped, which keeps every column on the same grid, and that is the
    precondition of ``02`` section 3.
    """
    level = np.cumsum(series)
    trailing = pd.Series(level).rolling(window).mean().to_numpy()
    signal = np.zeros(series.size, dtype=np.float64)
    signal[1:] = (level[:-1] > trailing[:-1]).astype(np.float64)
    return signal * series


def main() -> None:
    """Write ``tests/fixtures/trials.csv``, one column per window."""
    reference = pd.read_csv(FIXTURES / "reference_daily.csv")
    stamps = reference.iloc[:, 0].to_numpy()
    series = reference.iloc[:, 1].to_numpy(dtype=np.float64)

    columns = {"period_end": stamps}
    for window in WINDOWS:
        columns[f"ma_{window}"] = crossover_returns(series, window)

    frame = pd.DataFrame(columns)
    target = FIXTURES / "trials.csv"
    frame.to_csv(target, index=False, lineterminator="\n")

    sharpes = {
        name: float(values.mean() / values.std(ddof=1))
        for name, values in ((c, frame[c].to_numpy()) for c in frame.columns[1:])
    }
    best = max(sharpes, key=lambda k: sharpes[k])
    print(f"wrote {target.relative_to(ROOT)}: {len(WINDOWS)} configurations, {len(frame)} periods")
    print(f"best {best} at per period Sharpe {sharpes[best]:.4f}")
    print(f"median across configurations {np.median(list(sharpes.values())):.4f}")


if __name__ == "__main__":
    main()
