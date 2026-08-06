"""The approving path, which nothing exercised until D065.

Every end to end run this project had produced ended in a verdict that was
negative or suppressed, and the test covering it was named
``test_a_losing_strategy_still_gets_a_negative_verdict``. A tool that has only
ever been shown to say no is indistinguishable from a tool that cannot say
anything else, and the whole claim of the product is that it *decides*.

``trades_winner.csv`` is a log with real positive expectancy and
``trials_winner.csv`` is the twenty configuration sweep that produced it. The
tests below are mostly about **orderings**, not thresholds: a threshold on a
deflated probability would be a number chosen to pass, while an ordering like
"the search costs confidence" is the thing the tool exists to demonstrate and
holds for any input where it is working.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qvalid.pipeline import run_validation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = FIXTURES / "trades_winner.csv"
CONFIG = FIXTURES / "run_config_winner.yaml"

MULTIPLIER = 50.0
CAPITAL = 100_000.0
RISK_FREE = 0.02


@pytest.fixture(scope="module")
def report() -> object:
    """One run; the bootstrap dominates and nothing here mutates it."""
    return run_validation(LOG, CONFIG).report


def payload(report: object, name: str) -> dict:
    entry = report.entry(name)  # type: ignore[attr-defined]
    assert entry.status.value == "RAN", f"{name} did not run: {entry.reason}"
    return entry.payload


def independent_returns() -> np.ndarray:
    """Per period returns from the raw CSV, computed without the library.

    One trade per business day, so the trade series and the daily grid coincide
    and no regridding is needed for the comparison to be exact.
    """
    frame = pd.read_csv(LOG)
    side = np.where(frame["direction"] == "Long", 1.0, -1.0)
    gross = side * (frame["close_price"] - frame["open_price"]) * frame["quantity"] * MULTIPLIER
    return ((gross - frame["commission"]) / CAPITAL).to_numpy()


class TestItApproves:
    def test_the_verdict_is_positive(self, report: object) -> None:
        """The first one in this project's history. See D065."""
        assert payload(report, "verdict")["certainty_equivalent"] > 0.0

    def test_and_the_verdict_is_rankable_rather_than_suppressed(self, report: object) -> None:
        assert "verdict" not in report.sections_absent  # type: ignore[attr-defined]

    def test_the_sharpe_interval_excludes_zero(self, report: object) -> None:
        metrics = payload(report, "calendar_metrics")
        assert metrics["sharpe_ci_low"] > 0.0

    def test_the_track_record_is_already_long_enough(self, report: object) -> None:
        """The other side of D064: the section that could only report an
        infinite requirement now reports a met one."""
        track = payload(report, "track_record")
        assert track["attainable"] is True
        assert track["sufficient"] is True
        assert track["periods"] < track["observed_periods"]


class TestTheSearchCorrectionDoesItsJob:
    """Orderings, not thresholds. These are the product's actual argument."""

    def test_accounting_for_the_search_costs_real_confidence(self, report: object) -> None:
        """The single number this tool exists to produce.

        Against zero the Sharpe is all but certain; against the best of twenty
        configurations it is merely likely. A tool that reported only the first
        would be the defect the project was written to correct.
        """
        deflated = payload(report, "deflated_sharpe")
        assert deflated["probability_against_zero"] > deflated["probability"]

    def test_the_expected_maximum_sits_between_the_median_and_the_best(
        self, report: object
    ) -> None:
        """Otherwise the deflation is comparing against the wrong distribution."""
        deflated = payload(report, "deflated_sharpe")
        assert (
            deflated["trial_sharpe_median"]
            < deflated["expected_maximum"]
            < deflated["trial_sharpe_best"]
        )

    def test_the_declared_trial_count_matches_the_matrix(self, report: object) -> None:
        deflated = payload(report, "deflated_sharpe")
        assert deflated["n_trials_declared"] == deflated["n_trials_in_matrix"] == 20

    def test_overfitting_is_reported_and_below_a_coin_flip(self, report: object) -> None:
        """Not a tuned threshold: above one half the in sample winner would be
        below median out of sample more often than not, which for a genuine
        edge would mean the cross validation had failed to see it."""
        assert payload(report, "pbo")["probability"] < 0.5

    def test_the_logit_stays_under_its_ceiling(self, report: object) -> None:
        """D025's second error, kept as a guard: the logit cannot exceed
        log(N), so a median above the ceiling would be arithmetic gone wrong."""
        overfit = payload(report, "pbo")
        assert overfit["median_logit"] <= overfit["logit_ceiling"]


class TestTheNumbersAgreeWithArithmeticDoneOutside:
    """Computed from the CSV without importing what produced the report."""

    def test_the_cumulative_return(self, report: object) -> None:
        expected = float(independent_returns().sum())
        assert payload(report, "calendar_metrics")["cumulative_return"] == pytest.approx(expected)

    def test_the_annualised_sharpe_including_the_risk_free_convention(self, report: object) -> None:
        """Fails under ``rf / ppy``; the library converts geometrically, and
        D062 verified that from outside for the first time."""
        values = independent_returns()
        periods_per_year = report.grid["periods_per_year"]  # type: ignore[attr-defined]
        per_period = (1.0 + RISK_FREE) ** (1.0 / periods_per_year) - 1.0
        expected = (values.mean() - per_period) / values.std(ddof=1) * np.sqrt(periods_per_year)
        assert payload(report, "calendar_metrics")["sharpe_sqrt_q"] == pytest.approx(expected)

    def test_the_drawdown_is_shallower_than_the_total_gain(self, report: object) -> None:
        metrics = payload(report, "calendar_metrics")
        assert 0.0 < metrics["max_drawdown"] < metrics["cumulative_return"]


class TestTheFixtureIsWhatItClaims:
    """A winning fixture that quietly stopped winning would make the suite lie."""

    def test_the_log_satisfies_the_coherence_identity_exactly(self) -> None:
        """Otherwise the import would be checking a different arithmetic than
        the one the fixture was built with."""
        frame = pd.read_csv(LOG)
        side = np.where(frame["direction"] == "Long", 1.0, -1.0)
        gross = side * (frame["close_price"] - frame["open_price"]) * frame["quantity"] * MULTIPLIER
        residual = np.abs(gross - frame["commission"] - frame["net_pnl"])
        assert residual.max() < 1e-9

    def test_the_chosen_configuration_is_the_best_of_the_sweep(self) -> None:
        """The deflation answers a question about the winner of a search, so a
        trial matrix whose winner is some other column asks a different one."""
        trials = pd.read_csv(FIXTURES / "trials_winner.csv").drop(columns=["period_end"])
        sharpes = trials.mean() / trials.std(ddof=1)
        assert sharpes.idxmax() == "win_20"

    def test_the_sweep_is_aligned_with_the_log_period_for_period(self, report: object) -> None:
        trials = pd.read_csv(FIXTURES / "trials_winner.csv")
        assert len(trials) == report.grid["n_periods"]  # type: ignore[attr-defined]
