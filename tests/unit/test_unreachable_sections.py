"""Two whole modules that never reached a report, and a section that vanished. See D073.

``core/propfirm.py`` implements section 6 of ``02`` in full, and
``superior_predictive_ability`` implements Hansen (2005) of section 3.4. Both
were written, specified and tested, and the pipeline called neither, so no user
could reach either. That is the same defect D052 found in the trial matrix and
D056 in section 3.2, arriving for the third and fourth time.

Measured before the fix:

    funcoes publicas em core/propfirm.py    2
    chamadas a propfirm no pipeline         0
    chamadas a superior_predictive_ability  0

The third finding here was not planned. ``pbo`` was **absent from the panel**
rather than present and ``NOT_REQUESTED`` whenever no trial matrix was supplied,
and D031 says in as many words that the first is a bug of this pipeline while
the second is a declared absence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qvalid.core.propfirm import load_rules
from qvalid.exceptions import SchemaError
from qvalid.pipeline import run_validation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = FIXTURES / "trades_winner.csv"
RULES = FIXTURES / "desk_example.yaml"

SEARCH_DEPENDENT = ("deflated_sharpe", "pbo", "spa")
"""Every section that needs the trial matrix, and so shares one fate."""


def configuration(folder: Path, **extra: object) -> Path:
    for name in (
        "mapping_generic.yaml",
        "symbology.yaml",
        "trials_winner.csv",
        "desk_example.yaml",
    ):
        (folder / name).write_text((FIXTURES / name).read_text())
    path = folder / "run.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "symbology_path": "symbology.yaml",
                "mapping_path": "mapping_generic.yaml",
                "initial_capital": 50000.0,
                "basis": "FIXED_INITIAL",
                "seed": 20260806,
                "risk_free_rate": 0.0,
                "n_paths": 300,
                **extra,
            }
        )
    )
    return path


def full(folder: Path, **extra: object) -> Path:
    return configuration(
        folder,
        n_trials=20,
        trials_path="trials_winner.csv",
        propfirm_rules_path="desk_example.yaml",
        **extra,
    )


class TestEverySectionIsInThePanelWhateverHappened:
    """D031's rule, which the pipeline had been breaking for ``pbo``."""

    @pytest.mark.parametrize("name", [*SEARCH_DEPENDENT, "propfirm"])
    def test_it_is_present_even_when_its_input_is_absent(self, name: str, tmp_path: Path) -> None:
        report = run_validation(LOG, configuration(tmp_path)).report
        assert report.entry(name).status.value == "NOT_REQUESTED"

    def test_and_present_when_everything_was_supplied(self, tmp_path: Path) -> None:
        report = run_validation(LOG, full(tmp_path)).report
        for name in [*SEARCH_DEPENDENT, "propfirm"]:
            assert report.entry(name).status.value == "RAN", report.entry(name).reason

    def test_the_sections_that_share_an_input_share_a_fate(self, tmp_path: Path) -> None:
        """``pbo`` used to vanish while ``deflated_sharpe`` was declared absent,
        so a reader scanning for what did not run would have missed one."""
        report = run_validation(LOG, configuration(tmp_path, n_trials=20)).report
        statuses = {name: report.entry(name).status.value for name in SEARCH_DEPENDENT}
        assert len(set(statuses.values())) == 1, statuses

    def test_every_absent_section_states_a_reason(self, tmp_path: Path) -> None:
        report = run_validation(LOG, configuration(tmp_path)).report
        for name in report.sections_absent:
            assert report.entry(name).reason


class TestTheDeskSimulatorReachesTheReport:
    def test_it_reports_what_a_desk_would_ask(self, tmp_path: Path) -> None:
        payload = run_validation(LOG, full(tmp_path)).report.entry("propfirm").payload
        assert 0.0 <= payload["pass_probability"] <= 1.0
        assert 0.0 <= payload["payout_probability"] <= payload["pass_probability"]
        assert payload["expected_net_value"] is not None

    def test_the_payout_cannot_be_likelier_than_the_pass(self, tmp_path: Path) -> None:
        """A funded account is reached only through the evaluation, so the
        ordering is structural rather than statistical."""
        payload = run_validation(LOG, full(tmp_path)).report.entry("propfirm").payload
        assert payload["payout_probability"] <= payload["pass_probability"]

    def test_it_prints_when_the_rules_were_last_checked(self, tmp_path: Path) -> None:
        """Desk rules change. A stale file running in silence is the failure
        this project spends its time removing, so the date is in the report."""
        payload = run_validation(LOG, full(tmp_path)).report.entry("propfirm").payload
        assert payload["rules_verified_on"] == "2026-08-06"
        assert payload["rules_source"].startswith("https://")

    def test_no_rules_is_not_requested_rather_than_assumed(self, tmp_path: Path) -> None:
        """Whether a strategy passes an evaluation is a question about a desk,
        and there is no desk to assume."""
        entry = run_validation(LOG, configuration(tmp_path)).report.entry("propfirm")
        assert entry.status.value == "NOT_REQUESTED"
        assert "no desk to simulate against" in (entry.reason or "")
        assert "cannot be assumed" in (entry.reason or "")

    def test_a_coarser_grid_is_suppressed_with_the_two_periods(self, tmp_path: Path) -> None:
        """Desk rules are daily. On a weekly grid the daily loss limit and the
        order of the within day checks of D036 have nothing to act on, and that
        is an invalidity condition rather than an execution error."""
        config = full(tmp_path, forced_period="WEEKLY")
        entry = run_validation(LOG, config).report.entry("propfirm")
        assert entry.status.value == "SUPPRESSED"
        assert entry.observed == "WEEKLY"
        assert entry.threshold == "DAILY"


class TestRulesMustSayWhenTheyWereVerified:
    def test_a_file_without_the_date_is_refused(self, tmp_path: Path) -> None:
        text = RULES.read_text().replace("verified_on: 2026-08-06\n", "")
        path = tmp_path / "stale.yaml"
        path.write_text(text)
        with pytest.raises(SchemaError, match="verified_on"):
            load_rules(path)

    def test_a_file_without_the_source_is_refused(self, tmp_path: Path) -> None:
        text = RULES.read_text().replace("source_url: https://example.invalid/rules\n", "")
        path = tmp_path / "unsourced.yaml"
        path.write_text(text)
        with pytest.raises(SchemaError, match="source_url"):
            load_rules(path)

    def test_the_example_file_loads(self) -> None:
        rules = load_rules(RULES)
        assert rules.rules_id == "EXAMPLE-50K"
        assert rules.verified_on.isoformat() == "2026-08-06"


class TestSuperiorPredictiveAbilityReachesTheReport:
    """Section 3.4, built in v0.4 and never once run from the tool."""

    def test_it_runs_on_the_matrix_alone(self, tmp_path: Path) -> None:
        """No new input: a zero benchmark tests superiority over holding cash,
        which is the question a single strategy's owner is asking anyway."""
        payload = run_validation(LOG, full(tmp_path)).report.entry("spa").payload
        assert payload["benchmark"] == "zero, which is holding cash"
        assert payload["n_configs"] == 20

    def test_the_three_recentrings_bracket_the_one_to_read(self, tmp_path: Path) -> None:
        """``02`` 3.4 reports all three so the bracket is visible, and Hansen
        (2005) guarantees the ordering."""
        payload = run_validation(LOG, full(tmp_path)).report.entry("spa").payload
        assert payload["p_value_lower"] <= payload["p_value_consistent"] <= payload["p_value_upper"]

    def test_the_winning_configuration_is_named(self, tmp_path: Path) -> None:
        payload = run_validation(LOG, full(tmp_path)).report.entry("spa").payload
        assert payload["best_config"] == "win_20"

    def test_a_real_edge_is_found(self, tmp_path: Path) -> None:
        """Not a tuned threshold: the winning fixture beats cash by construction,
        and a test that could not see that would be testing nothing."""
        payload = run_validation(LOG, full(tmp_path)).report.entry("spa").payload
        assert payload["p_value_consistent"] < 0.05
