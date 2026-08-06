"""Reading the timestamp pattern out of the column. See D066.

The claim being tested is narrow and checkable: a pattern either reads every
stamp in the column or it does not. What makes it useful is the second half,
that testing the **whole column** usually settles day first against month
first, and that when it does not the answer is an admission rather than a pick.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qvalid.adapters.timeformats import CANDIDATE_FORMATS, matching_formats

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class TestOneRowIsNotEnoughAndAColumnUsuallyIs:
    """The reason this reads every row, measured on a real fixture."""

    def test_a_single_stamp_cannot_separate_day_first_from_month_first(self) -> None:
        """``08.03.2022`` is the eighth of March and also the third of August."""
        found = matching_formats(["08.03.2022 14:46:00"])
        assert found.ambiguous
        assert found.only is None
        assert "2022-03-08" in (found.disagreement or "")
        assert "2022-08-03" in (found.disagreement or "")

    def test_the_whole_column_of_the_same_file_settles_it(self) -> None:
        column = list(pd.read_csv(FIXTURES / "foreign_mt5.csv")["Open Time"])
        found = matching_formats(column)
        assert found.only == "%d.%m.%Y %H:%M:%S"
        assert not found.ambiguous

    def test_one_day_past_the_twelfth_is_all_it_takes(self) -> None:
        assert matching_formats(["05.03.2024 09:30:00"]).ambiguous
        assert matching_formats(["05.03.2024 09:30:00", "25.04.2024 10:00:00"]).only is not None

    def test_a_column_whose_days_never_exceed_twelve_stays_ambiguous(self) -> None:
        """A real property of that file, not a limitation of the check, and
        reported so the person can settle it from what they know."""
        found = matching_formats(["05.03.2024 09:30:00", "02.04.2024 10:00:00"])
        assert found.ambiguous
        assert found.disagreement is not None


class TestWhatItReads:
    def test_the_projects_own_fixture(self) -> None:
        column = list(pd.read_csv(FIXTURES / "trades_generic.csv")["opened_at"])
        assert matching_formats(column).only == "%Y-%m-%d %H:%M:%S"

    @pytest.mark.parametrize(
        ("sample", "expected"),
        [
            ("2024-12-25T09:30:00", "%Y-%m-%dT%H:%M:%S"),
            ("25/12/2024 09:30:00", "%d/%m/%Y %H:%M:%S"),
            ("20241225 09:30:00", "%Y%m%d %H:%M:%S"),
            ("25.12.2024", "%d.%m.%Y"),
        ],
    )
    def test_common_shapes(self, sample: str, expected: str) -> None:
        assert matching_formats([sample]).only == expected

    def test_surrounding_space_does_not_defeat_it(self) -> None:
        assert matching_formats(["  2024-12-25 09:30:00  "]).only == "%Y-%m-%d %H:%M:%S"

    def test_a_shape_it_does_not_know_is_no_match_rather_than_a_wrong_one(self) -> None:
        """Not a failure. The mapping still accepts any pattern typed by hand,
        and this only saves the person from writing one it could have shown."""
        found = matching_formats(["Mar 5 2024 at 9am"])
        assert found.parsing == ()
        assert found.only is None
        assert not found.ambiguous

    def test_one_unreadable_row_disqualifies_a_pattern_for_the_whole_column(self) -> None:
        """Otherwise the mapping would be chosen on a prefix and fail on import
        somewhere in the middle of a file nobody scrolled to."""
        assert matching_formats(["2024-12-25 09:30:00", "not a date"]).parsing == ()

    def test_no_samples_is_no_answer(self) -> None:
        found = matching_formats([])
        assert found.parsing == () and not found.ambiguous


class TestTheCandidateListIsHonest:
    def test_every_ambiguous_separator_carries_both_orderings(self) -> None:
        """The first version of this list had ``%m/%d/%Y`` but not
        ``%m.%d.%Y``, so a dotted American date reported one confident match
        because its rival was missing from the list being searched. An
        ambiguity detector is only as honest as its candidates."""
        for separator in (".", "/", "-"):
            day_first = f"%d{separator}%m{separator}%Y"
            month_first = f"%m{separator}%d{separator}%Y"
            has_day = any(pattern.startswith(day_first) for pattern in CANDIDATE_FORMATS)
            has_month = any(pattern.startswith(month_first) for pattern in CANDIDATE_FORMATS)
            assert has_day == has_month, f"{separator!r} carries only one ordering"

    def test_no_candidate_is_listed_twice(self) -> None:
        assert len(set(CANDIDATE_FORMATS)) == len(CANDIDATE_FORMATS)
