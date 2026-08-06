"""Recovering the multiplier, and the boundary where the file stops knowing. See D061.

The interesting tests are the ones that require a refusal. Anyone can invert an
identity; the claim worth checking is that the probe declines when the
arithmetic no longer carries the answer, because a confident wrong multiplier
is exactly the failure D007 exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from qvalid.adapters.probe import (
    COST_TO_QUANTUM_FLOOR,
    Detectability,
    implied_multipliers,
    probe_symbols,
    probe_trade_log,
    quantum_of,
)
from qvalid.adapters.tradelog import load_mapping
from qvalid.cli import app
from qvalid.exceptions import SchemaError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def synthetic(
    *,
    multiplier: float = 50.0,
    quantum: float = 0.01,
    fee_per_contract: float = 2.10,
    n: int = 400,
    gross_column: bool = False,
    seed: int = 20260805,
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    """A log obeying the identity exactly, then rounded the way a broker rounds."""
    rng = np.random.default_rng(seed)
    tick = 0.25
    qty = rng.integers(1, 5, n).astype(float)
    entry = np.round(rng.uniform(4000.0, 4600.0, n) / tick) * tick
    exit_ = entry + np.round(rng.normal(0.0, 6.0, n) / tick) * tick
    side = rng.choice([1.0, -1.0], n)
    move = side * (exit_ - entry) * qty
    gross = move * multiplier
    fees = np.round(fee_per_contract * qty * 2.0, 2)
    raw = gross if gross_column else gross - fees
    pnl = np.round(raw / quantum) * quantum
    return ["ES"] * n, pnl, fees, move


class TestRecoversWhatIsKnown:
    """The fixtures carry multipliers a person wrote down independently."""

    @pytest.mark.parametrize(
        ("name", "symbol", "expected"),
        [("trades_long.csv", "ESZ4", 50.0), ("trades_generic.csv", "NQZ4", 20.0)],
    )
    def test_matches_the_committed_symbology(self, name: str, symbol: str, expected: float) -> None:
        declared = yaml.safe_load((FIXTURES / "symbology.yaml").read_text())["symbols"]
        assert any(entry["source_ids"]["generic"] == symbol for entry in declared.values())
        found = probe_trade_log(FIXTURES / name, load_mapping(FIXTURES / "mapping_generic.yaml"))
        entry = next(item for item in found if item.symbol == symbol)
        assert entry.implied == pytest.approx(expected)

    def test_identifies_the_convention_the_fixture_was_written_in(self) -> None:
        found = probe_trade_log(
            FIXTURES / "trades_long.csv", load_mapping(FIXTURES / "mapping_generic.yaml")
        )
        entry = next(item for item in found if item.symbol == "ESZ4")
        assert entry.detectability is Detectability.DECISIVE
        assert entry.convention == "NET"

    def test_recovers_a_gross_column_as_gross(self) -> None:
        entry = probe_symbols(*synthetic(gross_column=True))[0]
        assert entry.convention == "GROSS"
        assert entry.implied == pytest.approx(50.0)

    def test_a_flat_trade_carries_no_information_and_is_dropped(self) -> None:
        """Counted against the array rather than against 90: the generator
        rounds to a tick, so a few moves land on zero on their own."""
        symbols, pnl, fees, move = synthetic(n=100)
        move[:10] = 0.0
        pnl[:10] = -fees[:10]
        found = probe_symbols(symbols, pnl, fees, move)[0]
        assert found.n_usable == int(np.count_nonzero(move))
        assert found.n_usable <= 90

    def test_each_symbol_is_probed_on_its_own_rows(self) -> None:
        a = synthetic(multiplier=50.0, n=200, seed=1)
        b = synthetic(multiplier=20.0, n=200, seed=2)
        found = probe_symbols(
            ["ES"] * 200 + ["NQ"] * 200,
            np.concatenate([a[1], b[1]]),
            np.concatenate([a[2], b[2]]),
            np.concatenate([a[3], b[3]]),
        )
        assert {item.symbol: round(item.implied, 6) for item in found} == {"ES": 50.0, "NQ": 20.0}

    def test_symbols_keep_the_order_of_the_file(self) -> None:
        found = probe_symbols(
            ["ZZ", "AA", "ZZ"], np.array([1.0, 1.0, 1.0]), np.zeros(3), np.array([1.0, 1.0, 1.0])
        )
        assert [item.symbol for item in found] == ["ZZ", "AA"]


class TestSaysWhenTheFileCannotKnow:
    """D017's blind spot, located rather than repeated."""

    def test_zero_costs_make_the_conventions_coincide(self) -> None:
        entry = probe_symbols(*synthetic(fee_per_contract=0.0))[0]
        assert entry.detectability is Detectability.NO_COST
        assert entry.convention is None

    def test_but_the_multiplier_survives_zero_costs(self) -> None:
        """What a missing cost destroys is the convention, not the multiplier."""
        assert probe_symbols(*synthetic(fee_per_contract=0.0))[0].implied == pytest.approx(50.0)

    def test_costs_below_the_rounding_of_the_pnl_column_are_refused(self) -> None:
        """A fee of 4.20 subtracted from a column rounded to the nearest 100
        leaves nothing behind to read."""
        entry = probe_symbols(*synthetic(quantum=100.0, fee_per_contract=2.10))[0]
        assert entry.detectability is Detectability.UNDETECTABLE
        assert entry.convention is None

    def test_and_the_multiplier_still_survives_that_too(self) -> None:
        """Approximately, not exactly. Measured over twelve seeds as relative
        error against a true 50, the worst case at this rounding was 1.1e-2;
        the first version of this test asserted 1e-3 and was simply wrong
        about how robust the inversion is."""
        entry = probe_symbols(*synthetic(quantum=100.0, fee_per_contract=2.10))[0]
        assert entry.is_readable
        assert entry.implied == pytest.approx(50.0, rel=2e-2)

    def test_rounding_that_swallows_the_trade_kills_the_multiplier_too(self) -> None:
        """The convention dies first because it rides on the fee; the
        multiplier rides on the whole P&L and dies later. Measured: relative
        error 6.5e-2 at a quantum of half a typical trade and 9.2e-1 at a
        whole one. A number that wrong which still looks like a number is the
        failure D007 was written about, so it is withheld."""
        entry = probe_symbols(*synthetic(quantum=2000.0, fee_per_contract=2.10))[0]
        assert not entry.is_readable
        assert np.isnan(entry.implied)

    def test_a_readable_column_stays_readable(self) -> None:
        entry = probe_symbols(*synthetic(quantum=0.01))[0]
        assert entry.is_readable
        assert entry.typical_pnl > 0.0
        # 0.1 and not the 0.01 the generator rounds to: gross P&L lands on
        # multiples of 12.5 and the fee on multiples of 0.1, so the column
        # never uses its last decimal. quantum_of reports the step the data
        # has, which is the point of reading it instead of declaring it.
        assert entry.pnl_quantum == pytest.approx(0.1)

    @pytest.mark.parametrize("quantum", [0.01, 0.1, 1.0, 10.0])
    def test_above_the_floor_it_answers_and_answers_correctly(self, quantum: float) -> None:
        """The floor is stated as a ratio, so the test walks the ratio.

        Measured when the floor was chosen, sweeping cost over quantum: the
        wrong convention's spread beats the right one's below 0.32 and loses
        above 0.63. The floor sits at 1.0, and these four points are all above
        it by construction of the fee.
        """
        fee = COST_TO_QUANTUM_FLOOR * quantum * 4.0
        entry = probe_symbols(*synthetic(quantum=quantum, fee_per_contract=fee))[0]
        assert entry.detectability is Detectability.DECISIVE
        assert entry.convention == "NET"

    @pytest.mark.parametrize("quantum", [1.0, 10.0, 100.0])
    def test_below_the_floor_it_never_claims_a_convention(self, quantum: float) -> None:
        fee = COST_TO_QUANTUM_FLOOR * quantum * 0.1
        assert (
            probe_symbols(*synthetic(quantum=quantum, fee_per_contract=fee))[0].convention is None
        )

    def test_detectability_is_decided_per_symbol_not_per_file(self) -> None:
        """One commission free instrument must not borrow the other's confidence."""
        paid = synthetic(n=200, fee_per_contract=2.10, seed=3)
        free = synthetic(n=200, fee_per_contract=0.0, seed=4)
        found = probe_symbols(
            ["PAID"] * 200 + ["FREE"] * 200,
            np.concatenate([paid[1], free[1]]),
            np.concatenate([paid[2], free[2]]),
            np.concatenate([paid[3], free[3]]),
        )
        assert {item.symbol: item.detectability for item in found} == {
            "PAID": Detectability.DECISIVE,
            "FREE": Detectability.NO_COST,
        }


class TestQuantum:
    def test_reads_the_rounding_the_column_actually_uses(self) -> None:
        assert quantum_of(np.array([12.34, -3.50, 10.75])) == pytest.approx(0.01)
        assert quantum_of(np.array([12.0, -3.0, 10.0])) == pytest.approx(1.0)
        assert quantum_of(np.array([1.234, 5.678])) == pytest.approx(0.001)

    def test_finds_a_column_rounded_coarser_than_one(self) -> None:
        """The version that started the search at 1.0 reported 1.0 here, and
        the detectability gate then compared a real cost against a rounding a
        hundred times finer than the true one."""
        assert quantum_of(np.array([-500.0, 1300.0, 900.0])) == pytest.approx(100.0)

    def test_a_non_decimal_quantum_errs_towards_refusing(self) -> None:
        """Quarters are not a power of ten, so the finest one that divides them
        is reported. That understates the step, which makes the gate refuse
        more often than needed and never less."""
        assert quantum_of(np.array([1.25, -3.50, 10.75])) == pytest.approx(0.01)

    def test_a_large_account_is_not_mistaken_for_a_ragged_one(self) -> None:
        """Dividing millions by a cent loses bits, and a fixed tolerance would
        report a clean column as ragged once the numbers got big enough."""
        assert quantum_of(np.round(np.linspace(1e6, 9e6, 500), 2)) <= 0.01

    def test_an_empty_column_reports_absence_not_fine_resolution(self) -> None:
        assert quantum_of(np.array([])) == 0.0

    def test_an_all_zero_column_reports_absence_too(self) -> None:
        """Every step divides zero, so without a guard the search returns its
        coarsest candidate and calls a column of nothing extremely coarse."""
        assert quantum_of(np.zeros(10)) == 0.0


class TestRefusesMalformedInput:
    def test_arrays_of_different_lengths(self) -> None:
        with pytest.raises(SchemaError):
            probe_symbols(["A", "B"], np.ones(2), np.ones(3), np.ones(2))

    def test_a_mapping_without_every_term_of_the_identity(self, tmp_path: Path) -> None:
        text = (
            (FIXTURES / "mapping_generic.yaml").read_text().replace("  exit_px: close_price\n", "")
        )
        broken = tmp_path / "m.yaml"
        broken.write_text(text)
        with pytest.raises(SchemaError, match="exit_px"):
            probe_trade_log(FIXTURES / "trades_long.csv", load_mapping(broken))

    def test_a_mapping_naming_a_column_the_file_does_not_have(self, tmp_path: Path) -> None:
        text = (FIXTURES / "mapping_generic.yaml").read_text().replace("close_price", "absent_col")
        broken = tmp_path / "m.yaml"
        broken.write_text(text)
        with pytest.raises(SchemaError, match="absent_col"):
            probe_trade_log(FIXTURES / "trades_long.csv", load_mapping(broken))

    def test_an_unrecognised_side_token(self, tmp_path: Path) -> None:
        frame = pd.read_csv(FIXTURES / "trades_long.csv")
        frame.loc[0, "direction"] = "sideways"
        log = tmp_path / "odd.csv"
        frame.to_csv(log, index=False)
        with pytest.raises(SchemaError, match="sideways"):
            probe_trade_log(log, load_mapping(FIXTURES / "mapping_generic.yaml"))

    def test_inversion_is_aligned_between_the_two_conventions(self) -> None:
        net, gross = implied_multipliers(
            np.array([10.0, 0.0, 30.0]), np.array([1.0, 1.0, 1.0]), np.array([1.0, 0.0, 3.0])
        )
        assert net.size == gross.size == 2


class TestProbeCommand:
    def test_leaves_the_multiplier_slot_empty(self) -> None:
        """D007 in one assertion. The implied value appears only in a comment,
        so a person who saves this file without reading gets a validation
        error, not a plausible report."""
        result = CliRunner().invoke(
            app,
            [
                "probe",
                str(FIXTURES / "trades_long.csv"),
                "-m",
                str(FIXTURES / "mapping_generic.yaml"),
            ],
        )
        assert result.exit_code == 0
        line = next(item for item in result.stdout.splitlines() if "multiplier:" in item)
        before_comment = line.split("#")[0]
        assert before_comment.strip() == "multiplier:"
        assert "50" in line

    def test_the_draft_does_not_parse_into_a_usable_symbology(self) -> None:
        """Stronger than reading the text: the emitted YAML is deliberately
        incomplete, and that is what stops it being adopted unread."""
        result = CliRunner().invoke(
            app,
            [
                "probe",
                str(FIXTURES / "trades_long.csv"),
                "-m",
                str(FIXTURES / "mapping_generic.yaml"),
            ],
        )
        parsed = yaml.safe_load(result.stdout)
        assert parsed["symbols"]["ESZ4"]["multiplier"] is None
        assert parsed["symbols"]["ESZ4"]["tick_size"] is None

    def test_writes_nothing(self, tmp_path: Path) -> None:
        log = tmp_path / "trades.csv"
        log.write_text((FIXTURES / "trades_long.csv").read_text())
        before = set(tmp_path.iterdir())
        CliRunner().invoke(app, ["probe", str(log), "-m", str(FIXTURES / "mapping_generic.yaml")])
        assert set(tmp_path.iterdir()) == before

    def test_a_missing_file_is_an_error(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            app,
            ["probe", str(tmp_path / "absent.csv"), "-m", str(FIXTURES / "mapping_generic.yaml")],
        )
        assert result.exit_code == 2
