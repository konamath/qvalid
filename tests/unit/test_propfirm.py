"""Tests for the proprietary desk simulator of section 6 of ``02``.

``TestDeterministicCases`` is the criterion ``05`` v0.8 states: a deterministic
path must produce the exact expected result, with no tolerance at all. Every
barrier gets one, because a barrier model that is right on average and wrong on
the boundary is wrong.

``TestThreeDesksFromFilesAlone`` is the project requirement of ``02`` section 6:
three distinct rule sets, configured only by YAML, with no Python touched.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from qvalid.contracts import EquityPaths, Period, Unit
from qvalid.core.propfirm import (
    EvaluationOutcome,
    PropFirmRules,
    evaluate,
    load_rules,
)
from qvalid.exceptions import InsufficientSampleError, SchemaError, UnitMismatchError

DESKS = Path(__file__).resolve().parents[1] / "fixtures" / "propfirm"
STATIC = DESKS / "static_drawdown.yaml"
TRAILING = DESKS / "trailing_drawdown.yaml"
PATIENT = DESKS / "patient_desk.yaml"


def paths_from_daily(
    daily: np.ndarray, *, unit: Unit = Unit.PERIOD, period: Period | None = Period.DAILY
) -> EquityPaths:
    """Wrap a matrix of daily P&L as equity paths. Only the differences are used."""
    daily = np.atleast_2d(np.asarray(daily, dtype=np.float64))
    levels = np.empty((daily.shape[0], daily.shape[1] + 1), dtype=np.float64)
    levels[:, 0] = 0.0
    np.cumsum(daily, axis=1, out=levels[:, 1:])
    return EquityPaths(
        values=np.ascontiguousarray(levels),
        unit=unit,
        seed=1,
        method="test",
        period=period if unit is Unit.PERIOD else None,
    )


class TestDeterministicCases:
    """``05`` v0.8: exact results, no tolerance."""

    def test_a_constant_positive_day_passes_on_the_exact_day(self) -> None:
        """3000 target at 400 a day is day 8, and the minimum of 5 days is already met."""
        result = evaluate(paths_from_daily(np.full((3, 60), 400.0)), load_rules(STATIC))
        assert result.pass_probability == 1.0
        assert result.days_to_pass[0.5] == 8.0
        assert result.outcome_counts == {"passed": 3}

    def test_the_minimum_trading_day_rule_delays_a_fast_pass(self) -> None:
        """Reaching the target in two days does not pass a desk that wants five.

        The target is cleared on day two and the pass waits for day five, which
        is the whole point of the rule: the desk refuses to fund someone who got
        there in two lucky sessions.
        """
        daily = np.zeros((1, 30))
        daily[0, :2] = 2000.0
        daily[0, 2:6] = 1.0
        result = evaluate(paths_from_daily(daily), load_rules(STATIC))
        assert result.pass_probability == 1.0
        assert result.days_to_pass[0.5] == 5.0

    def test_the_daily_loss_limit_fires_on_the_exact_day(self) -> None:
        daily = np.zeros((1, 30))
        daily[0, 3] = -1000.01
        result = evaluate(paths_from_daily(daily), load_rules(STATIC))
        assert result.outcome_counts == {"failed_daily_loss": 1}
        assert result.pass_probability == 0.0

    def test_a_loss_exactly_at_the_daily_limit_does_not_fire(self) -> None:
        """The rule is strict: losing the limit is allowed, losing more is not."""
        daily = np.zeros((1, 30))
        daily[0, 3] = -1000.0
        result = evaluate(paths_from_daily(daily), load_rules(STATIC))
        assert "failed_daily_loss" not in result.outcome_counts

    def test_the_static_maximum_loss_fires_at_the_exact_level(self) -> None:
        daily = np.full((1, 30), -500.0)
        result = evaluate(paths_from_daily(daily), load_rules(STATIC))
        assert result.outcome_counts == {"failed_max_loss": 1}

    def test_trailing_is_stricter_than_static_on_the_same_path(self) -> None:
        """The single most consequential field on the form, shown on one path.

        A path that rises 1500 and then gives back 2000 sits at minus 500 from
        the start, which a static limit of 2000 tolerates, and at exactly 2000
        below the peak, which a trailing one does not.
        """
        daily = np.zeros((1, 30))
        daily[0, 0] = 1500.0
        daily[0, 1:5] = -500.0
        static = evaluate(paths_from_daily(daily), load_rules(STATIC))
        trailing = evaluate(paths_from_daily(daily), load_rules(TRAILING))
        assert "failed_max_loss" not in static.outcome_counts
        assert trailing.outcome_counts.get("failed_max_loss") == 1

    def test_a_flat_path_ends_unfinished_rather_than_failed(self) -> None:
        """Never breaching and never reaching is a third outcome, not a failure."""
        result = evaluate(paths_from_daily(np.zeros((4, 30))), load_rules(PATIENT))
        assert result.outcome_counts == {"unfinished": 4}
        assert result.pass_probability == 0.0
        assert result.days_to_pass == {}

    def test_the_time_limit_ends_a_slow_path(self) -> None:
        daily = np.full((1, 90), 10.0)
        result = evaluate(paths_from_daily(daily), load_rules(STATIC))
        assert result.outcome_counts == {"failed_time_limit": 1}

    def test_the_fee_is_charged_on_every_path_including_the_failures(self) -> None:
        """Averaging only over survivors would answer a flattering question."""
        result = evaluate(paths_from_daily(np.full((5, 30), -500.0)), load_rules(PATIENT))
        assert result.expected_net_value == pytest.approx(-500.0)

    def test_the_refund_lands_only_on_a_pass(self) -> None:
        rules = load_rules(STATIC)
        assert rules.fee_refunded_on_pass is True
        result = evaluate(paths_from_daily(np.full((2, 60), 400.0)), rules)
        assert result.expected_net_value >= 0.0

    def test_a_payout_arrives_on_the_exact_cycle_day(self) -> None:
        """Passing on day 8, the first 14 day cycle closes on day 22."""
        daily = np.full((1, 60), 400.0)
        result = evaluate(paths_from_daily(daily), load_rules(STATIC))
        assert result.payout_probability == 1.0
        assert result.days_to_first_payout[0.5] == 22.0

    def test_a_profit_below_the_minimum_payout_carries_instead_of_paying(self) -> None:
        rules = load_rules(STATIC)
        daily = np.full((1, 60), 400.0)
        daily[0, 8:] = 1.0
        result = evaluate(paths_from_daily(daily), rules)
        assert result.pass_probability == 1.0
        assert result.payout_probability == 0.0


class TestTheUnitGuard:
    """``02`` section 6: the rules are daily, so the paths must be."""

    def test_trade_indexed_paths_are_refused(self) -> None:
        paths = paths_from_daily(np.full((4, 30), 100.0), unit=Unit.TRADE, period=None)
        with pytest.raises(UnitMismatchError, match="unit=PERIOD"):
            evaluate(paths, load_rules(STATIC))

    def test_a_weekly_grid_is_refused(self) -> None:
        paths = paths_from_daily(np.full((4, 30), 100.0), period=Period.WEEKLY)
        with pytest.raises(UnitMismatchError, match="period=DAILY"):
            evaluate(paths, load_rules(STATIC))

    def test_a_path_without_a_day_is_refused(self) -> None:
        paths = EquityPaths(
            values=np.zeros((3, 1), dtype=np.float64),
            unit=Unit.PERIOD,
            seed=1,
            method="test",
            period=Period.DAILY,
        )
        with pytest.raises(InsufficientSampleError, match="at least one day"):
            evaluate(paths, load_rules(STATIC))


class TestThreeDesksFromFilesAlone:
    """``02`` section 6 project requirement, and ``05`` v0.8 acceptance."""

    @pytest.fixture
    def noisy(self):
        return paths_from_daily(np.random.default_rng(2026).normal(90.0, 700.0, (4_000, 120)))

    def test_the_three_desks_load_and_differ(self) -> None:
        rules = [load_rules(path) for path in (STATIC, TRAILING, PATIENT)]
        assert len({r.rules_id for r in rules}) == 3
        assert [r.trailing for r in rules] == [False, True, False]
        assert {r.account_size for r in rules} == {50_000.0, 100_000.0}

    def test_each_desk_produces_its_own_answer(self, noisy) -> None:
        results = {
            load_rules(path).rules_id: evaluate(noisy, load_rules(path))
            for path in (STATIC, TRAILING, PATIENT)
        }
        assert len({r.pass_probability for r in results.values()}) == 3
        for result in results.values():
            assert 0.0 <= result.pass_probability <= 1.0
            assert result.n_paths == 4_000
            assert result.horizon_days == 120

    def test_trailing_kills_more_by_drawdown_than_static_does(self, noisy) -> None:
        """The same paths, the same target, the same limit, a stricter rule."""
        static = evaluate(noisy, load_rules(STATIC))
        trailing = evaluate(noisy, load_rules(TRAILING))
        assert trailing.outcome_counts["failed_max_loss"] > static.outcome_counts["failed_max_loss"]
        assert trailing.pass_probability < static.pass_probability

    def test_a_desk_without_a_time_limit_leaves_paths_unfinished(self, noisy) -> None:
        patient = evaluate(noisy, load_rules(PATIENT))
        assert patient.outcome_counts.get("unfinished", 0) > 0
        assert "failed_time_limit" not in patient.outcome_counts

    def test_the_outcome_counts_add_up_to_every_path(self, noisy) -> None:
        for path in (STATIC, TRAILING, PATIENT):
            result = evaluate(noisy, load_rules(path))
            assert sum(result.outcome_counts.values()) == result.n_paths

    def test_no_path_is_left_running(self, noisy) -> None:
        """``RUNNING`` is an internal state and must never reach the report."""
        for path in (STATIC, TRAILING, PATIENT):
            assert "running" not in evaluate(noisy, load_rules(path)).outcome_counts


class TestInvariances:
    def test_the_result_is_deterministic(self) -> None:
        paths = paths_from_daily(np.random.default_rng(7).normal(50.0, 400.0, (200, 60)))
        first = evaluate(paths, load_rules(STATIC))
        second = evaluate(paths, load_rules(STATIC))
        assert first.pass_probability == second.pass_probability
        assert first.expected_net_value == second.expected_net_value

    def test_the_strategy_capital_does_not_enter(self) -> None:
        """Only the daily differences are read, so shifting the level changes nothing."""
        daily = np.random.default_rng(11).normal(50.0, 400.0, (200, 60))
        base = evaluate(paths_from_daily(daily), load_rules(STATIC))
        shifted_levels = np.asarray(paths_from_daily(daily).values) + 1_000_000.0
        shifted = evaluate(
            EquityPaths(
                values=np.ascontiguousarray(shifted_levels),
                unit=Unit.PERIOD,
                seed=1,
                method="test",
                period=Period.DAILY,
            ),
            load_rules(STATIC),
        )
        assert shifted.pass_probability == base.pass_probability

    def test_a_larger_target_can_only_lower_the_pass_probability(self) -> None:
        paths = paths_from_daily(np.random.default_rng(13).normal(60.0, 500.0, (2_000, 90)))
        payload = yaml.safe_load(STATIC.read_text(encoding="utf-8"))
        probabilities = []
        for target in (1_000.0, 3_000.0, 9_000.0):
            payload["profit_target"] = target
            probabilities.append(
                evaluate(paths, PropFirmRules.model_validate(payload)).pass_probability
            )
        assert probabilities == sorted(probabilities, reverse=True)

    def test_a_tighter_daily_limit_can_only_lower_the_pass_probability(self) -> None:
        paths = paths_from_daily(np.random.default_rng(17).normal(60.0, 500.0, (2_000, 90)))
        payload = yaml.safe_load(STATIC.read_text(encoding="utf-8"))
        probabilities = []
        for limit in (400.0, 1_000.0, 1_900.0):
            payload["daily_loss_limit"] = limit
            probabilities.append(
                evaluate(paths, PropFirmRules.model_validate(payload)).pass_probability
            )
        assert probabilities == sorted(probabilities)

    def test_percentiles_of_the_net_value_are_ordered(self) -> None:
        paths = paths_from_daily(np.random.default_rng(19).normal(80.0, 600.0, (2_000, 120)))
        result = evaluate(paths, load_rules(STATIC))
        keys = sorted(result.net_value_percentiles)
        assert [result.net_value_percentiles[k] for k in keys] == sorted(
            result.net_value_percentiles[k] for k in keys
        )


class TestRuleValidation:
    def _payload(self, source: Path = STATIC, **overrides):
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        payload.update(overrides)
        return payload

    def _write(self, tmp_path: Path, payload) -> Path:
        path = tmp_path / "desk.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    def test_a_daily_limit_above_the_maximum_loss_is_refused(self, tmp_path: Path) -> None:
        """It can never bind, so it is a transcription error rather than an exotic desk."""
        with pytest.raises(SchemaError, match="can never bind"):
            load_rules(self._write(tmp_path, self._payload(daily_loss_limit=5_000.0)))

    def test_an_unsatisfiable_day_requirement_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="cannot be passed"):
            load_rules(
                self._write(tmp_path, self._payload(min_trading_days=90, max_evaluation_days=60))
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("account_size", 0.0),
            ("profit_target", -1.0),
            ("max_loss", 0.0),
            ("evaluation_fee", -1.0),
            ("min_trading_days", -1),
        ],
    )
    def test_impossible_numbers_are_refused(self, tmp_path: Path, field: str, value: float) -> None:
        with pytest.raises(SchemaError):
            load_rules(self._write(tmp_path, self._payload(**{field: value})))

    def test_a_profit_split_outside_the_unit_interval_is_refused(self, tmp_path: Path) -> None:
        payload = self._payload()
        payload["funded"]["profit_split"] = 1.5
        with pytest.raises(SchemaError):
            load_rules(self._write(tmp_path, payload))

    def test_an_unknown_key_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match=r"[Ee]xtra"):
            load_rules(self._write(tmp_path, self._payload(consistency_rule=0.5)))

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="not found"):
            load_rules(tmp_path / "absent.yaml")

    def test_malformed_yaml_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("funded: [unclosed\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="not valid YAML"):
            load_rules(path)

    def test_a_non_mapping_document_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="must be a mapping"):
            load_rules(path)

    def test_the_outcome_labels_cover_every_code(self) -> None:
        codes = {
            EvaluationOutcome.RUNNING,
            EvaluationOutcome.PASSED,
            EvaluationOutcome.FAILED_DAILY_LOSS,
            EvaluationOutcome.FAILED_MAX_LOSS,
            EvaluationOutcome.FAILED_TIME_LIMIT,
            EvaluationOutcome.UNFINISHED,
        }
        assert codes == set(EvaluationOutcome.LABELS)


class TestFundedPhase:
    def test_a_funded_account_can_be_blown_after_passing(self) -> None:
        """Passing is not the end: the funded account has its own barriers."""
        daily = np.zeros((1, 60))
        daily[0, :8] = 400.0
        daily[0, 8:14] = -400.0
        result = evaluate(paths_from_daily(daily), load_rules(STATIC))
        assert result.pass_probability == 1.0
        assert result.payout_probability == 0.0

    def test_the_split_is_applied_to_the_payout(self) -> None:
        rules = load_rules(STATIC)
        daily = np.zeros((1, 40))
        daily[0, :8] = 400.0
        daily[0, 10] = 1_000.0
        result = evaluate(paths_from_daily(daily), rules)
        expected = 1_000.0 * rules.funded.profit_split - rules.evaluation_fee
        assert result.expected_net_value == pytest.approx(expected + rules.evaluation_fee, rel=1e-9)

    def test_a_short_horizon_understates_payouts_and_says_so(self) -> None:
        """The horizon is reported next to the payout numbers for this reason."""
        daily = np.full((1, 20), 400.0)
        short = evaluate(paths_from_daily(daily), load_rules(STATIC))
        long = evaluate(paths_from_daily(np.full((1, 120), 400.0)), load_rules(STATIC))
        assert short.horizon_days == 20
        assert long.horizon_days == 120
        assert long.expected_net_value > short.expected_net_value
