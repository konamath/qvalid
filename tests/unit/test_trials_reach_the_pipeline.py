"""D052. The section ``02`` calls the most important one, finally reachable.

Until this, declaring ``n_trials`` in a run configuration bought a second
flavour of ``NOT_REQUESTED``: the deflation needs the dispersion across trial
Sharpe ratios, and there was no way to hand the pipeline a trial matrix. The
capability the whole project is built around could not be reached from the
tool's own entry point.

``TestTheCorrectionActuallyBites`` is the demonstration. On the shipped
fixtures the probability that the true Sharpe is positive falls from 0.060 to
0.0004 once twenty searched configurations are accounted for. A tool that
reported only the first number would be the defect this project exists to
correct.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from qvalid.pipeline import run_validation
from qvalid.report.model import EvidenceStatus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FROZEN = "2026-08-05T00:00:00Z"


@pytest.fixture(scope="module")
def run():
    return run_validation(
        FIXTURES / "trades_long.csv", FIXTURES / "run_config_trials.yaml", executed_at=FROZEN
    )


def entry(report, name: str):
    return next(item for item in report.panel if item.name == name)


class TestTheCorrectionActuallyBites:
    def test_all_three_sections_run(self, run) -> None:
        """Before D052 the first was absent and the other two unreachable."""
        for name in ("deflated_sharpe", "pbo", "verdict"):
            assert entry(run.report, name).status is EvidenceStatus.RAN

    def test_the_search_correction_changes_the_answer(self, run) -> None:
        """The whole argument of ``02`` section 3, in two numbers.

        Six per cent becomes four in ten thousand once the twenty
        configurations tried before this one are accounted for.

        These numbers moved when D055 put both sides of the deflation on excess
        returns. Before that the undeflated probability read 0.40 while the
        headline Sharpe was -0.91, which no reader could reconcile, because the
        two were different quantities under one name.
        """
        payload = entry(run.report, "deflated_sharpe").payload
        assert payload["probability_against_zero"] == pytest.approx(0.060, abs=0.005)
        assert payload["probability"] == pytest.approx(0.0004, abs=0.0003)
        assert payload["probability"] < payload["probability_against_zero"] / 50.0

    def test_the_deflation_agrees_in_sign_with_the_headline_sharpe(self, run) -> None:
        """D055. A negative Sharpe and a probable positive true Sharpe cannot both hold.

        The check that would have caught the convention mismatch immediately,
        and that nothing performed: two sections of one report answering the
        same question in opposite directions.
        """
        sharpe = entry(run.report, "calendar_metrics").payload["sharpe_sqrt_q"]
        probability = entry(run.report, "deflated_sharpe").payload["probability_against_zero"]
        assert sharpe < 0.0
        assert probability < 0.5

    def test_the_deflation_uses_per_period_sharpes(self, run) -> None:
        """Annualising here would inflate the dispersion by the periods per year.

        The variance across trials is of per period Sharpe ratios, and the
        observed Sharpe it is compared against is per period too. A daily grid
        would put a factor of about 250 between the two if one side were
        annualised, and the resulting probability would still read as a
        probability.
        """
        payload = entry(run.report, "deflated_sharpe").payload
        assert 0.0 < payload["trial_variance"] < 0.01
        assert abs(payload["trial_sharpe_best"]) < 1.0
        assert payload["trial_sharpe_best"] > payload["trial_sharpe_median"]

    def test_the_pbo_reports_its_own_ceiling(self, run) -> None:
        """D025: the logit is bounded by log(N), so the magnitude needs its bound."""
        payload = entry(run.report, "pbo").payload
        assert payload["logit_ceiling"] == pytest.approx(np.log(20))
        assert payload["median_logit"] <= payload["logit_ceiling"]
        assert payload["n_combinations"] > 1_000

    def test_the_verdict_becomes_reachable(self, run) -> None:
        """D039 made the verdict wait for the deflation. Now the wait can end."""
        payload = entry(run.report, "verdict").payload
        assert payload["certainty_equivalent"] is not None
        assert payload["requirements_are_default"] is True

    def test_a_losing_strategy_still_gets_a_negative_verdict(self, run) -> None:
        """Reachable is not the same as flattering, and the fixture is a loser."""
        assert entry(run.report, "verdict").payload["certainty_equivalent"] < 0.0


class TestTheMatrixIsRefusedWhenItCannotBeTrusted:
    def test_a_matrix_off_the_grid_is_refused(self, tmp_path: Path) -> None:
        """Positional alignment would hand the tests a matrix that looks aligned."""
        import pandas as pd

        frame = pd.read_csv(FIXTURES / "trials.csv")
        frame = frame.iloc[5:]
        shifted = tmp_path / "trials.csv"
        frame.to_csv(shifted, index=False, lineterminator="\n")
        config = tmp_path / "run.yaml"
        original = (FIXTURES / "run_config_trials.yaml").read_text(encoding="utf-8")
        config.write_text(original.replace("trials.csv", str(shifted)), encoding="utf-8")
        for name in ("symbology.yaml", "mapping_generic.yaml", "reference_daily.csv"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())

        result = run_validation(FIXTURES / "trades_long.csv", config, executed_at=FROZEN)
        deflated = entry(result.report, "deflated_sharpe")
        assert deflated.status is EvidenceStatus.FAILED
        assert "missing" in (deflated.reason or "")

    def test_a_single_column_matrix_is_refused(self, tmp_path: Path) -> None:
        """Dispersion across one trial does not exist, and pretending it is zero lies."""
        import pandas as pd

        frame = pd.read_csv(FIXTURES / "trials.csv").iloc[:, :2]
        thin = tmp_path / "trials.csv"
        frame.to_csv(thin, index=False, lineterminator="\n")
        config = tmp_path / "run.yaml"
        original = (FIXTURES / "run_config_trials.yaml").read_text(encoding="utf-8")
        config.write_text(original.replace("trials.csv", str(thin)), encoding="utf-8")
        for name in ("symbology.yaml", "mapping_generic.yaml", "reference_daily.csv"):
            (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())

        result = run_validation(FIXTURES / "trades_long.csv", config, executed_at=FROZEN)
        assert entry(result.report, "deflated_sharpe").status is EvidenceStatus.FAILED


class TestTheTrackRecordLengthReachesTheReport:
    """``02`` section 3.2, which lived in ``core`` with tests and never printed.

    Same class of gap as D052 and found by the same question: which of the
    things ``02`` specifies can a person actually get out of the tool. Unlike
    the deflation it needs no trial matrix, so it runs on every input.
    """

    def test_the_section_exists_at_all(self, run) -> None:
        assert any(item.name == "track_record" for item in run.report.panel)

    def test_a_negative_sharpe_gets_infinity_rather_than_a_large_number(self, run) -> None:
        """The honest answer, and the one a finite number would hide.

        No length of record makes a Sharpe below the benchmark significantly
        above it. Printing ten thousand periods would invite the reader to plan
        for a wait that does not end.
        """
        entry_ = entry(run.report, "track_record")
        assert entry_.status is EvidenceStatus.FAILED
        assert "infinite rather than long" in (entry_.reason or "")

    def test_a_winning_strategy_gets_a_finite_length(self, tmp_path: Path) -> None:
        """Otherwise the section would be failing for a reason other than the Sharpe."""
        import numpy as np

        from qvalid.contracts import Basis, Period, PeriodReturns
        from qvalid.core.constants import WEEKDAYS_PER_YEAR
        from qvalid.core.overfit import minimum_track_record_length

        rng = np.random.default_rng(3)
        values = np.ascontiguousarray(rng.normal(0.002, 0.01, 900))
        day = 86_400 * 1_000_000_000
        series = PeriodReturns(
            values=values,
            period_end_ns=1_600_000_000 * 1_000_000_000 + np.arange(900, dtype=np.int64) * day,
            period=Period.DAILY,
            periods_per_year=WEEKDAYS_PER_YEAR,
            calendar_id="TEST",
            basis=Basis.FIXED_INITIAL,
            initial_capital=100_000.0,
            n_active=900,
        )
        assert minimum_track_record_length(series).periods > 0.0

    def test_a_higher_risk_free_rate_demands_a_longer_record(self) -> None:
        """D055 again, one level down, and stated as a monotonicity.

        Before this the function computed a raw Sharpe while the report's
        headline was excess, so the required length ignored the alternative the
        strategy is being compared against. Asserting an ordering rather than a
        threshold means the test does not depend on a realised sample mean that
        happens to sit either side of a chosen rate.
        """
        import numpy as np

        from qvalid.contracts import Basis, Period, PeriodReturns
        from qvalid.core.constants import WEEKDAYS_PER_YEAR
        from qvalid.core.overfit import minimum_track_record_length

        rng = np.random.default_rng(5)
        values = np.ascontiguousarray(rng.normal(0.00015, 0.01, 1200))
        day = 86_400 * 1_000_000_000
        series = PeriodReturns(
            values=values,
            period_end_ns=1_600_000_000 * 1_000_000_000 + np.arange(1200, dtype=np.int64) * day,
            period=Period.DAILY,
            periods_per_year=WEEKDAYS_PER_YEAR,
            calendar_id="TEST",
            basis=Basis.FIXED_INITIAL,
            initial_capital=100_000.0,
            n_active=1200,
        )
        lengths = [
            minimum_track_record_length(series, risk_free_rate=rate).periods
            for rate in (0.0, 0.02, 0.05)
        ]
        assert lengths == sorted(lengths)
        # Measured: 1664, 2547, 5981 periods. The bound is stated at two so the
        # test pins an effect of consequence rather than a rounding difference,
        # and leaves room for the sampling error of the realised mean.
        assert lengths[-1] > lengths[0] * 2.0
