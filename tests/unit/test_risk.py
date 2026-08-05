"""Tests for the risk measures of section 5 of ``02``.

The four kinds required by ``04``:

``TestAnalyticCases``
    A deterministic path hits the barrier at an exact step, with no tolerance
    at all. The simulated ruin probability matches the continuity corrected
    closed form within Monte Carlo error and misses the uncorrected one by many
    standard errors. The expected maximum drawdown matches the doubly corrected
    form of Magdon-Ismail et al.

``TestInvariances``
    Determinism, scale, the inequality between expected shortfall and value at
    risk, and monotonicity of the tail level.

``TestDegenerateCases``
    Constant paths, a barrier above the starting equity, a barrier never
    reached, one step, too few paths for a tail quantile, and ruin under a
    percentage basis.

``TestSyntheticRecovery``
    Kelly recovered from a declared per step mean and variance, and the
    coverage of the bootstrap interval on the ruin probability.

``TestUnitGuard``
    The prohibition of ``02`` section 5: a horizon measured in trades is not a
    horizon.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from qvalid.contracts import Basis, EquityPaths, Period, Unit
from qvalid.core.risk import (
    DEFAULT_TAIL_LEVEL,
    MonteCarloEstimate,
    absorb_at_barrier,
    brownian_ruin_probability,
    drawdown_distribution,
    expected_max_drawdown,
    expected_shortfall,
    first_passage,
    kelly_from_paths,
    max_drawdown_per_path,
    path_returns,
    terminal_return,
    value_at_risk,
)
from qvalid.exceptions import InsufficientSampleError, UnitMismatchError

START = 1_000.0


def make_paths(
    levels: np.ndarray,
    *,
    unit: Unit = Unit.PERIOD,
    period: Period | None = Period.DAILY,
) -> EquityPaths:
    return EquityPaths(
        values=np.ascontiguousarray(levels, dtype=np.float64),
        unit=unit,
        seed=0,
        method="test",
        period=period if unit is Unit.PERIOD else None,
    )


def gaussian_paths(
    n_paths: int,
    n_steps: int,
    seed: int,
    *,
    mu: float = 0.0,
    sigma: float = 1.0,
    start: float = START,
) -> EquityPaths:
    """Additive Gaussian walk in levels, which is the driftless reference process."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(mu, sigma, (n_paths, n_steps))
    levels = np.empty((n_paths, n_steps + 1), dtype=np.float64)
    levels[:, 0] = start
    np.cumsum(steps, axis=1, out=levels[:, 1:])
    levels[:, 1:] += start
    return make_paths(levels)


class TestAnalyticCases:
    def test_deterministic_path_hits_the_barrier_at_the_exact_step(self) -> None:
        """No tolerance. With a step of ``-d`` the first passage is at ``ceil(a/d)``."""
        for step, distance in ((1.0, 23.0), (2.5, 30.0), (0.5, 7.5)):
            n_steps = 200
            levels = np.tile(START - step * np.arange(n_steps + 1, dtype=np.float64), (3, 1))
            passage = first_passage(make_paths(levels), barrier=START - distance, seed=1)
            expected = math.ceil(distance / step)
            assert set(np.asarray(passage.steps_to_barrier).tolist()) == {expected}
            assert passage.probability.value == 1.0
            assert passage.never_hit_fraction == 0.0

    @pytest.mark.parametrize(("n_steps", "distance"), [(252, 20.0), (252, 10.0), (60, 8.0)])
    def test_ruin_matches_the_continuity_corrected_closed_form(
        self, n_steps: int, distance: float
    ) -> None:
        """``02`` section 5, restated by D022.

        The uncorrected reflection principle is the wrong reference for a
        barrier monitored once per step, and it is wrong by far more than the
        Monte Carlo error, so a test written against it would have to be given
        a loose tolerance, which ``04`` forbids.
        """
        paths = gaussian_paths(60_000, n_steps, seed=11, sigma=1.0)
        passage = first_passage(paths, barrier=START - distance, seed=12)
        simulated = passage.probability.value
        standard_error = math.sqrt(simulated * (1.0 - simulated) / passage.probability.n_paths)
        corrected = brownian_ruin_probability(distance, 1.0, n_steps)
        naive = brownian_ruin_probability(distance, 1.0, n_steps, continuity_corrected=False)

        assert abs(simulated - corrected) < 4.0 * standard_error
        assert abs(simulated - naive) > 4.0 * standard_error
        assert simulated < naive

    def test_expected_max_drawdown_matches_the_doubly_corrected_form(self) -> None:
        """The continuity correction enters twice, once per monitored boundary."""
        for n_steps in (60, 252, 1_000):
            paths = gaussian_paths(20_000, n_steps, seed=13, sigma=1.0, start=1e9)
            simulated = float(max_drawdown_per_path(paths).mean()) * 1e9
            corrected = expected_max_drawdown(1.0, n_steps)
            naive = expected_max_drawdown(1.0, n_steps, continuity_corrected=False)
            assert simulated == pytest.approx(corrected, rel=0.01)
            assert simulated < naive

    def test_the_two_closed_forms_differ_in_the_declared_direction(self) -> None:
        assert brownian_ruin_probability(20.0, 1.0, 252) < brownian_ruin_probability(
            20.0, 1.0, 252, continuity_corrected=False
        )
        assert expected_max_drawdown(1.0, 252) < expected_max_drawdown(
            1.0, 252, continuity_corrected=False
        )

    def test_terminal_return_is_a_ratio_of_levels(self) -> None:
        levels = np.array([[100.0, 110.0, 120.0], [100.0, 90.0, 50.0]])
        np.testing.assert_allclose(terminal_return(make_paths(levels)), [0.2, -0.5])

    def test_max_drawdown_on_a_known_path(self) -> None:
        levels = np.array([[100.0, 120.0, 60.0, 90.0]])
        assert float(max_drawdown_per_path(make_paths(levels))[0]) == pytest.approx(0.5)

    def test_path_returns_differ_by_basis(self) -> None:
        """The basis is not recoverable from levels, which is why it is an argument."""
        levels = np.array([[100.0, 110.0, 121.0]])
        paths = make_paths(levels)
        np.testing.assert_allclose(path_returns(paths, Basis.FIXED_INITIAL), [[0.10, 0.11]])
        np.testing.assert_allclose(path_returns(paths, Basis.CURRENT_EQUITY), [[0.10, 0.10]])

    def test_absorption_freezes_the_path_from_first_passage(self) -> None:
        levels = np.array([[100.0, 95.0, 89.0, 120.0, 130.0]])
        absorbed = absorb_at_barrier(make_paths(levels), 90.0)
        np.testing.assert_allclose(np.asarray(absorbed.values)[0], [100.0, 95.0, 90.0, 90.0, 90.0])
        assert "absorbed" in absorbed.method
        assert absorbed.seed == 0


class TestInvariances:
    def test_expected_shortfall_is_never_below_value_at_risk(self) -> None:
        """Coherence, asserted rather than argued, on several shapes of tail."""
        for seed, mu in ((21, 0.0), (22, 0.5), (23, -0.5)):
            paths = gaussian_paths(4_000, 120, seed=seed, mu=mu)
            for level in (0.90, 0.95, 0.99):
                var = value_at_risk(paths, level=level, seed=7)
                shortfall = expected_shortfall(paths, level=level, seed=7)
                assert shortfall.value >= var.value

    def test_value_at_risk_is_monotone_in_the_level(self) -> None:
        paths = gaussian_paths(4_000, 120, seed=24)
        values = [value_at_risk(paths, level=q, seed=7).value for q in (0.80, 0.90, 0.95, 0.99)]
        assert values == sorted(values)

    def test_scale_invariance_of_the_tail_measures(self) -> None:
        base = gaussian_paths(2_000, 100, seed=25, sigma=1.0, start=START)
        scaled = make_paths(np.asarray(base.values) * 7.0)
        for level in (0.90, 0.95):
            assert value_at_risk(scaled, level=level, seed=7).value == pytest.approx(
                value_at_risk(base, level=level, seed=7).value, rel=1e-12
            )
            assert expected_shortfall(scaled, level=level, seed=7).value == pytest.approx(
                expected_shortfall(base, level=level, seed=7).value, rel=1e-12
            )

    def test_same_seed_gives_the_same_interval(self) -> None:
        paths = gaussian_paths(2_000, 100, seed=26)
        first = value_at_risk(paths, seed=99)
        second = value_at_risk(paths, seed=99)
        assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)
        # The bootstrap distribution of an empirical quantile is discrete and
        # lumpy: the endpoints land on order statistics of the original sample,
        # so two seeds often agree on one endpoint. The pair must still differ.
        other = value_at_risk(paths, seed=100)
        assert (other.ci_low, other.ci_high) != (first.ci_low, first.ci_high)

    def test_every_public_estimate_carries_its_uncertainty(self) -> None:
        """``02``: no quantile without the uncertainty of the quantile."""
        paths = gaussian_paths(2_000, 100, seed=27)
        estimates = [
            value_at_risk(paths, seed=1),
            expected_shortfall(paths, seed=1),
            drawdown_distribution(paths, seed=1).mean,
            first_passage(paths, barrier=START - 30.0, seed=1).probability,
        ]
        for estimate in estimates:
            assert isinstance(estimate, MonteCarloEstimate)
            assert estimate.standard_error > 0.0
            assert estimate.ci_low <= estimate.value <= estimate.ci_high

    def test_absorption_bounds_the_terminal_from_below_at_the_barrier(self) -> None:
        """Absorption is not uniformly conservative, and this test says so.

        Freezing at the barrier models a stop out executed exactly at the
        barrier. It lowers the terminal value of every path that breached and
        recovered, and it *raises* the terminal value of every path that ended
        below the barrier, because such a path would have been closed there.
        The deep tail therefore looks better after absorption, not worse. Any
        reading of an absorbed expected shortfall as the conservative number is
        wrong, and the test pins the direction so nobody has to guess.
        """
        paths = gaussian_paths(1_000, 150, seed=28)
        barrier = START - 15.0
        raw_levels = np.asarray(paths.values)
        absorbed_paths = absorb_at_barrier(paths, barrier)
        absorbed_levels = np.asarray(absorbed_paths.values)

        assert float(absorbed_levels.min()) >= barrier - 1e-12
        untouched = (raw_levels > barrier).all(axis=1)
        np.testing.assert_allclose(
            absorbed_levels[untouched], raw_levels[untouched], rtol=0.0, atol=0.0
        )
        breached = ~untouched
        assert bool(breached.any())
        ended_below = breached & (raw_levels[:, -1] < barrier)
        assert bool(
            np.all(
                terminal_return(absorbed_paths)[ended_below]
                > terminal_return(paths)[ended_below] - 1e-12
            )
        )


class TestDegenerateCases:
    def test_constant_paths_have_no_drawdown_and_no_ruin(self) -> None:
        paths = make_paths(np.full((50, 20), START))
        assert float(max_drawdown_per_path(paths).max()) == 0.0
        passage = first_passage(paths, barrier=START - 1.0, seed=1)
        assert passage.probability.value == 0.0
        assert passage.quantiles == {}
        assert passage.never_hit_fraction == 1.0

    def test_barrier_at_or_above_the_start_is_refused(self) -> None:
        paths = gaussian_paths(100, 20, seed=31)
        with pytest.raises(InsufficientSampleError, match="at or above the starting equity"):
            first_passage(paths, barrier=START, seed=1)

    def test_single_step_paths_are_allowed(self) -> None:
        levels = np.array([[START, START - 5.0], [START, START + 5.0]])
        passage = first_passage(make_paths(levels), barrier=START - 1.0, seed=1)
        assert passage.probability.value == pytest.approx(0.5)
        assert passage.horizon_steps == 1

    def test_an_empty_path_set_is_refused(self) -> None:
        empty = EquityPaths(
            values=np.zeros((0, 5), dtype=np.float64),
            unit=Unit.PERIOD,
            seed=0,
            method="test",
            period=Period.DAILY,
        )
        with pytest.raises(InsufficientSampleError, match="too few simulated paths"):
            terminal_return(empty)

    def test_paths_without_a_step_are_refused(self) -> None:
        with pytest.raises(InsufficientSampleError, match="at least one step"):
            terminal_return(make_paths(np.full((5, 1), START)))

    def test_too_few_paths_for_a_tail_quantile(self) -> None:
        paths = gaussian_paths(10, 50, seed=32)
        with pytest.raises(InsufficientSampleError, match="single order statistic"):
            value_at_risk(paths, seed=1)
        with pytest.raises(InsufficientSampleError, match="handful of order statistics"):
            expected_shortfall(paths, seed=1)

    def test_level_outside_the_unit_interval_is_refused(self) -> None:
        paths = gaussian_paths(100, 50, seed=33)
        for level in (0.0, 1.0, 1.5):
            with pytest.raises(InsufficientSampleError, match="open interval"):
                value_at_risk(paths, level=level, seed=1)

    def test_drawdown_on_a_ruined_path_is_refused_with_a_pointer(self) -> None:
        levels = np.array([[START, START / 2.0, -10.0]])
        with pytest.raises(InsufficientSampleError, match="Use first_passage"):
            max_drawdown_per_path(make_paths(levels))

    def test_kelly_needs_dispersion(self) -> None:
        paths = make_paths(np.tile(np.arange(11, dtype=np.float64) + START, (4, 1)))
        with pytest.raises(InsufficientSampleError, match="unbounded"):
            kelly_from_paths(paths, Basis.FIXED_INITIAL)

    def test_closed_forms_refuse_impossible_arguments(self) -> None:
        with pytest.raises(InsufficientSampleError):
            brownian_ruin_probability(0.0, 1.0, 10)
        with pytest.raises(InsufficientSampleError):
            brownian_ruin_probability(1.0, 0.0, 10)
        with pytest.raises(InsufficientSampleError):
            expected_max_drawdown(0.0, 10)
        with pytest.raises(InsufficientSampleError):
            expected_max_drawdown(1.0, 0)

    def test_all_paths_ruined_gives_probability_one(self) -> None:
        levels = np.tile(np.array([START, 1.0, 1.0]), (30, 1))
        passage = first_passage(make_paths(levels), barrier=10.0, seed=1)
        assert passage.probability.value == 1.0
        assert passage.probability.standard_error == 0.0
        assert passage.quantiles[0.5] == 1.0

    def test_observed_drawdown_without_a_value_leaves_the_quantile_empty(self) -> None:
        distribution = drawdown_distribution(gaussian_paths(200, 50, seed=34), seed=1)
        assert distribution.observed is None
        assert distribution.observed_quantile is None


class TestUnitGuard:
    def test_trade_indexed_paths_are_refused_for_a_declared_horizon(self) -> None:
        """``02`` section 5: a horizon measured in trades is not a horizon."""
        levels = np.tile(np.linspace(START, START - 50.0, 30), (40, 1))
        paths = make_paths(levels, unit=Unit.TRADE, period=None)
        with pytest.raises(UnitMismatchError, match="unit=PERIOD"):
            first_passage(paths, barrier=START - 10.0, seed=1)

    def test_trade_indexed_paths_are_fine_for_measures_without_a_horizon(self) -> None:
        """Drawdown and terminal return carry no calendar meaning, so they pass."""
        levels = np.tile(np.linspace(START, START - 50.0, 30), (40, 1))
        paths = make_paths(levels, unit=Unit.TRADE, period=None)
        assert float(max_drawdown_per_path(paths).max()) > 0.0
        assert float(terminal_return(paths)[0]) < 0.0


class TestSyntheticRecovery:
    @pytest.mark.parametrize(("mu", "truth"), [(0.0, 0.0), (0.0004, 4.0), (0.0012, 12.0)])
    def test_kelly_recovers_the_declared_fraction(self, mu: float, truth: float) -> None:
        """``mu / sigma^2`` with both declared, so the target is known exactly."""
        paths = gaussian_paths(4_000, 1_000, seed=41, mu=mu, sigma=0.01, start=1.0)
        estimate = kelly_from_paths(paths, Basis.FIXED_INITIAL)
        assert estimate.point == pytest.approx(truth, abs=0.25)
        assert estimate.adjusted < estimate.quantiles[0.5]

    def test_kelly_adjustment_is_conservative_even_under_a_real_edge(self) -> None:
        """A negative adjusted fraction under a true edge of 4 is the correct instruction."""
        paths = gaussian_paths(4_000, 1_000, seed=42, mu=0.0004, sigma=0.01, start=1.0)
        estimate = kelly_from_paths(paths, Basis.FIXED_INITIAL)
        assert estimate.point > 3.0
        assert estimate.adjusted < estimate.point

    def test_ruin_interval_covers_the_closed_form_at_the_nominal_rate(self) -> None:
        """Coverage of the bootstrap interval against a target that is known."""
        n_steps, distance = 120, 12.0
        target = brownian_ruin_probability(distance, 1.0, n_steps)
        covered = 0
        trials = 120
        for seed in range(trials):
            paths = gaussian_paths(1_500, n_steps, seed=7_000 + seed)
            passage = first_passage(paths, barrier=START - distance, seed=seed, n_replications=400)
            covered += int(passage.probability.ci_low <= target <= passage.probability.ci_high)
        rate = covered / trials
        standard_error = math.sqrt(0.95 * 0.05 / trials)
        assert abs(rate - 0.95) < 4.0 * standard_error, rate

    def test_observed_drawdown_is_positioned_as_a_quantile(self) -> None:
        """The criterion of ``05`` v0.3: the backtest number located in its own distribution."""
        paths = gaussian_paths(5_000, 252, seed=43, sigma=1.0)
        sample = max_drawdown_per_path(paths)
        median = float(np.median(sample))
        distribution = drawdown_distribution(paths, seed=1, observed=median)
        assert distribution.observed_quantile == pytest.approx(0.5, abs=0.02)
        benign = drawdown_distribution(paths, seed=1, observed=float(np.quantile(sample, 0.1)))
        assert benign.observed_quantile == pytest.approx(0.1, abs=0.02)

    def test_quantiles_of_the_drawdown_distribution_are_ordered(self) -> None:
        distribution = drawdown_distribution(gaussian_paths(2_000, 100, seed=44), seed=1)
        keys = sorted(distribution.quantiles)
        values = [distribution.quantiles[k] for k in keys]
        assert values == sorted(values)

    def test_time_to_barrier_quantiles_are_conditional_on_hitting(self) -> None:
        paths = gaussian_paths(20_000, 252, seed=45, sigma=1.0)
        passage = first_passage(paths, barrier=START - 20.0, seed=1)
        steps = np.asarray(passage.steps_to_barrier)
        hit = steps[steps >= 0]
        assert passage.quantiles[0.5] == pytest.approx(float(np.median(hit)))
        assert passage.never_hit_fraction == pytest.approx(1.0 - hit.size / steps.size)
        assert 0.0 < passage.probability.value < 1.0

    def test_default_tail_level_is_the_declared_one(self) -> None:
        assert DEFAULT_TAIL_LEVEL == 0.95
