"""D048. The unit assumption that made 2022 into 1970.

``TestTheResolutionIsStatedNotInherited`` is the regression test, and its point
is that it reproduces the failure **on any pandas version**. The bug only
appeared on CI, where pandas infers microsecond resolution from ISO strings.
Waiting for a pandas upgrade to test the fix would leave the fix unverified for
however long that takes, so the tests force each resolution explicitly with
``.dt.as_unit`` and assert the answer does not move.

``TestTheBanIsStructural`` stops the pattern coming back. ``astype("int64")``
on a datetime column is only correct under an assumption the code cannot see,
so it is banned outside the one function whose job is to state it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qvalid.adapters.timestamps import to_utc_nanos_from_pandas
from qvalid.exceptions import SchemaError

SOURCE = Path(__file__).resolve().parents[2] / "src" / "qvalid"
ISO = ["2022-01-03 21:00:00+00:00", "2022-01-04 21:00:00+00:00", "2024-01-02 21:00:00+00:00"]
EXPECTED = np.array([1_641_243_600, 1_641_330_000, 1_704_229_200], dtype=np.int64) * 1_000_000_000


class TestTheResolutionIsStatedNotInherited:
    @pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
    def test_every_storage_resolution_gives_the_same_nanoseconds(self, unit: str) -> None:
        """The whole bug, in one parametrisation.

        Before the fix this passed for ``ns`` and failed for the other three,
        which is precisely why it survived: the local pandas chose ``ns``.
        """
        stamps = pd.to_datetime(pd.Series(ISO)).dt.as_unit(unit)
        np.testing.assert_array_equal(to_utc_nanos_from_pandas(stamps, source="test"), EXPECTED)

    def test_the_old_expression_really_did_produce_1970(self) -> None:
        """Pin the failure itself, so the entry in ``06`` is checkable and not a story."""
        stamps = pd.to_datetime(pd.Series(ISO)).dt.as_unit("us")
        wrong = stamps.dt.tz_convert("UTC").astype("int64").to_numpy()
        assert pd.Timestamp(int(wrong[0]), tz="UTC").year == 1970
        assert (
            pd.Timestamp(int(to_utc_nanos_from_pandas(stamps, source="t")[0]), tz="UTC").year
            == 2022
        )

    def test_a_non_utc_zone_is_converted_rather_than_truncated(self) -> None:
        eastern = pd.to_datetime(pd.Series(ISO)).dt.tz_convert("America/New_York")
        np.testing.assert_array_equal(to_utc_nanos_from_pandas(eastern, source="t"), EXPECTED)

    def test_the_result_is_contiguous_int64(self) -> None:
        out = to_utc_nanos_from_pandas(pd.to_datetime(pd.Series(ISO)), source="t")
        assert out.dtype == np.int64
        assert out.flags["C_CONTIGUOUS"]

    def test_an_empty_column_is_allowed_and_stays_int64(self) -> None:
        empty = pd.to_datetime(pd.Series([], dtype="object"), utc=True)
        out = to_utc_nanos_from_pandas(empty, source="t")
        assert out.size == 0
        assert out.dtype == np.int64


class TestNaiveIsRefused:
    def test_a_naive_column_raises_rather_than_assuming_utc(self) -> None:
        naive = pd.to_datetime(pd.Series(["2022-01-03 21:00:00"]))
        with pytest.raises(SchemaError, match="naive"):
            to_utc_nanos_from_pandas(naive, source="somewhere.csv")

    def test_the_error_names_the_source(self) -> None:
        naive = pd.to_datetime(pd.Series(["2022-01-03 21:00:00"]))
        with pytest.raises(SchemaError, match=r"somewhere\.csv"):
            to_utc_nanos_from_pandas(naive, source="somewhere.csv")

    def test_a_column_that_is_not_a_datetime_raises(self) -> None:
        with pytest.raises(SchemaError, match="did not parse"):
            to_utc_nanos_from_pandas(pd.Series([1, 2, 3]), source="t")


class TestTheBanIsStructural:
    def test_no_module_but_this_one_casts_a_datetime_to_int64(self) -> None:
        """``astype("int64")`` is correct only under an unstated assumption.

        Reviewing for it is the discipline this project does not rely on. The
        syntax tree is cheaper and cannot forget.
        """
        offenders = [
            f"{path.relative_to(SOURCE)}:{node.lineno}"
            for path in sorted(SOURCE.rglob("*.py"))
            if path.name != "timestamps.py"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "astype"
            and any(isinstance(arg, ast.Constant) and arg.value == "int64" for arg in node.args)
        ]
        assert offenders == [], (
            f"astype('int64') outside adapters/timestamps.py at {offenders}. On a datetime "
            "column the result is nanoseconds or microseconds depending on what the parser "
            "inferred, and the caller cannot tell. Use to_utc_nanos_from_pandas. See D048."
        )
