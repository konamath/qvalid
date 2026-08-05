"""D052. The section ``02`` calls the most important one, finally reachable.

Until this, declaring ``n_trials`` in a run configuration bought a second
flavour of ``NOT_REQUESTED``: the deflation needs the dispersion across trial
Sharpe ratios, and there was no way to hand the pipeline a trial matrix. The
capability the whole project is built around could not be reached from the
tool's own entry point.

``TestTheCorrectionActuallyBites`` is the demonstration. On the shipped
fixtures the probability that the true Sharpe is positive falls from 0.40 to
0.02 once twenty searched configurations are accounted for. A tool that
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

        Undeflated, the strategy looks like a four in ten chance of a positive
        true Sharpe. Deflated by the twenty configurations that were tried
        before this one was chosen, it is one in fifty.
        """
        payload = entry(run.report, "deflated_sharpe").payload
        assert payload["probability_against_zero"] == pytest.approx(0.40, abs=0.02)
        assert payload["probability"] == pytest.approx(0.020, abs=0.005)
        assert payload["probability"] < payload["probability_against_zero"] / 10.0

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
