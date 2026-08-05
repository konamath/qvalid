"""The one place a pandas datetime column becomes int64 UTC nanoseconds.

``01`` rule 2 says timestamps are ``int64`` nanoseconds since the epoch in UTC,
and the reason given there is that a unit assumption made silently is the
hardest kind of error to see. This module exists because the project made
exactly that error.

What went wrong
---------------
Two modules wrote ``series.dt.tz_convert("UTC").astype("int64")`` and treated
the result as nanoseconds. That is true when pandas stores the column at
nanosecond resolution and false when it stores it at microsecond resolution,
which newer pandas infers from ISO strings. The same file then parses to
timestamps a **thousand times too small**: ``2022-01-03`` becomes
``1970-01-19``.

It did not fail quietly. The reference series stopped aligning with the grid
and every regime label was refused, because ``core/regimes.py`` aligns by exact
timestamp match rather than by position, which is what D032 changed it to after
the same class of error. The alignment check is what turned a silent thousandfold
unit error into a loud one. See D048.

The fix is one call: ``.dt.as_unit("ns")`` states the resolution instead of
inheriting whatever the parser chose.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qvalid.contracts import IntArray
from qvalid.exceptions import SchemaError

__all__ = ["to_utc_nanos_from_pandas"]


def to_utc_nanos_from_pandas(stamps: pd.Series, *, source: str) -> IntArray:
    """Convert a timezone aware pandas datetime column to int64 UTC nanoseconds.

    Parameters
    ----------
    stamps : pandas.Series
        Datetime column. Must be timezone aware; ``01`` forbids naive
        timestamps at every boundary, and a naive column here would be
        reinterpreted as local time by whatever machine happens to run it.
    source : str
        Path or identifier, for the error message. A unit error found six
        modules later is worth naming its origin.

    Returns
    -------
    numpy.ndarray of int64
        Nanoseconds since the Unix epoch, in UTC.

    Raises
    ------
    SchemaError
        If the column is not datetime typed, or is naive.

    Notes
    -----
    ``.dt.as_unit("ns")`` is the whole point. Without it the result carries
    whatever resolution the parser inferred, and the caller cannot tell the
    difference between nanoseconds and microseconds by looking at the number.
    """
    if not isinstance(
        stamps.dtype, pd.DatetimeTZDtype
    ) and not pd.api.types.is_datetime64_any_dtype(stamps):
        raise SchemaError(f"the timestamps at {source} did not parse to a datetime column")
    if stamps.dt.tz is None:
        raise SchemaError(
            f"the timestamps at {source} are naive; 01 forbids naive timestamps at every "
            "boundary, because a naive value is reinterpreted as local time by the machine "
            "that happens to run the job"
        )
    nanos = stamps.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").to_numpy()
    return np.ascontiguousarray(nanos, dtype=np.int64)
