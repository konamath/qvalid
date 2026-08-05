"""Tests for the symbology map and the generic CSV importer.

The importer is the only place where a wrong answer can be produced without any
downstream check noticing. Every convention is therefore tested for being
refused when undeclared, not merely for working when declared, and the two that
look alike are separated on purpose: a wrong fee *sign* is caught loudly by the
non negativity invariant, while a P&L column that is gross rather than net
passes silently under the one tick tolerance. See D017.

Fixtures live in ``tests/fixtures`` and are versioned, per the prohibition in
``04`` on tests depending on files outside it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml

from qvalid.adapters.symbology import SymbologyMap, load_symbology
from qvalid.adapters.tradelog import (
    REQUIRED_FIELDS,
    ColumnMapping,
    FeeConvention,
    PnlSource,
    load_mapping,
    read_trade_log_csv,
)
from qvalid.contracts import Side
from qvalid.exceptions import SchemaError, TradeIntegrityError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SYMBOLOGY_YAML = FIXTURES / "symbology.yaml"
MAPPING_YAML = FIXTURES / "mapping_generic.yaml"
TRADES_CSV = FIXTURES / "trades_generic.csv"


@pytest.fixture
def symbology() -> SymbologyMap:
    return load_symbology(SYMBOLOGY_YAML)


@pytest.fixture
def mapping() -> ColumnMapping:
    return load_mapping(MAPPING_YAML)


def write_yaml(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def mapping_payload(**overrides: object) -> dict[str, object]:
    payload = yaml.safe_load(MAPPING_YAML.read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


class TestSymbology:
    def test_loads_and_exposes_tick_sizes(self, symbology: SymbologyMap) -> None:
        assert symbology.tick_sizes() == {"ES": 0.25, "NQ": 0.25, "FDAX": 0.5}
        assert symbology.symbols["ES"].multiplier == 50.0
        assert symbology.symbols["ES"].currency == "USD"

    def test_resolves_a_source_ticker(self, symbology: SymbologyMap) -> None:
        assert symbology.resolve("generic", "ESZ4") == "ES"
        assert symbology.resolve("tradingview", "CME_MINI:ES1!") == "ES"

    def test_canonical_symbol_passes_through(self, symbology: SymbologyMap) -> None:
        assert symbology.resolve("generic", "ES") == "ES"

    def test_unknown_ticker_is_refused_and_lists_what_is_known(
        self, symbology: SymbologyMap
    ) -> None:
        with pytest.raises(SchemaError, match="not in the symbology map") as excinfo:
            symbology.resolve("generic", "ESH5")
        assert "ESZ4" in str(excinfo.value)

    def test_mixed_currency_is_refused(self, symbology: SymbologyMap) -> None:
        with pytest.raises(SchemaError, match="mixes currencies"):
            symbology.require_single_currency(["ES", "FDAX"])

    def test_single_currency_passes(self, symbology: SymbologyMap) -> None:
        assert symbology.require_single_currency(["ES", "NQ"]) == "USD"

    def test_ambiguous_reverse_index_is_refused(self, tmp_path: Path) -> None:
        path = write_yaml(
            tmp_path / "ambiguous.yaml",
            {
                "symbols": {
                    "ES": {
                        "venue": "CME",
                        "multiplier": 50.0,
                        "tick_size": 0.25,
                        "currency": "USD",
                        "calendar": "WEEKDAYS_UTC",
                        "source_ids": {"generic": "SAME"},
                    },
                    "NQ": {
                        "venue": "CME",
                        "multiplier": 20.0,
                        "tick_size": 0.25,
                        "currency": "USD",
                        "calendar": "WEEKDAYS_UTC",
                        "source_ids": {"generic": "SAME"},
                    },
                }
            },
        )
        with pytest.raises(SchemaError, match="ambiguous"):
            load_symbology(path).resolve("generic", "SAME")

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("multiplier", 0.0, "greater than 0"),
            ("tick_size", -1.0, "greater than 0"),
            ("currency", "DOLLAR", "three letter code"),
        ],
    )
    def test_invalid_spec_fields_are_refused(
        self, tmp_path: Path, field: str, value: object, match: str
    ) -> None:
        spec = {
            "venue": "CME",
            "multiplier": 50.0,
            "tick_size": 0.25,
            "currency": "USD",
            "calendar": "WEEKDAYS_UTC",
        }
        spec[field] = value
        path = write_yaml(tmp_path / "bad.yaml", {"symbols": {"ES": spec}})
        with pytest.raises(SchemaError, match=match):
            load_symbology(path)

    def test_empty_map_is_refused(self, tmp_path: Path) -> None:
        path = write_yaml(tmp_path / "empty.yaml", {"symbols": {}})
        with pytest.raises(SchemaError, match="at least one symbol"):
            load_symbology(path)

    def test_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="not found"):
            load_symbology(tmp_path / "nope.yaml")

    def test_malformed_yaml_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.yaml"
        path.write_text("symbols: [unclosed\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="not valid YAML"):
            load_symbology(path)

    def test_non_mapping_document_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- ES\n- NQ\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="must be a mapping"):
            load_symbology(path)

    def test_unknown_field_is_refused(self, tmp_path: Path) -> None:
        """``extra=forbid``: a typo in a YAML key must not be silently ignored."""
        path = write_yaml(
            tmp_path / "typo.yaml",
            {
                "symbols": {
                    "ES": {
                        "venue": "CME",
                        "multipler": 50.0,
                        "multiplier": 50.0,
                        "tick_size": 0.25,
                        "currency": "USD",
                        "calendar": "WEEKDAYS_UTC",
                    }
                }
            },
        )
        with pytest.raises(SchemaError, match=r"[Ee]xtra"):
            load_symbology(path)


class TestMappingValidation:
    def test_loads_the_fixture(self, mapping: ColumnMapping) -> None:
        assert mapping.source == "generic"
        assert mapping.fee_convention is FeeConvention.MAGNITUDE
        assert mapping.pnl_source is PnlSource.COLUMN
        assert mapping.timezone == "America/New_York"

    def test_every_required_field_must_be_bound(self, tmp_path: Path) -> None:
        payload = mapping_payload()
        del payload["columns"]["exit_px"]
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="required fields"):
            load_mapping(path)

    def test_unknown_canonical_field_is_refused(self, tmp_path: Path) -> None:
        payload = mapping_payload()
        payload["columns"]["slippage"] = "slip"
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="unknown canonical fields"):
            load_mapping(path)

    def test_column_pnl_source_requires_a_pnl_column(self, tmp_path: Path) -> None:
        payload = mapping_payload()
        del payload["columns"]["pnl"]
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="requires the mapping to bind"):
            load_mapping(path)

    def test_side_tokens_may_not_overlap(self, tmp_path: Path) -> None:
        payload = mapping_payload(side_long=["long", "B"], side_short=["short", "b"])
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="claimed by both directions"):
            load_mapping(path)

    def test_unknown_timezone_is_refused(self, tmp_path: Path) -> None:
        payload = mapping_payload(timezone="Mars/Olympus_Mons")
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="unknown IANA timezone"):
            load_mapping(path)

    def test_fee_convention_has_no_default(self, tmp_path: Path) -> None:
        """The identity cannot catch a fee sign error, so it must be declared."""
        payload = mapping_payload()
        del payload["fee_convention"]
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="fee_convention"):
            load_mapping(path)

    def test_pnl_source_has_no_default(self, tmp_path: Path) -> None:
        payload = mapping_payload()
        del payload["pnl_source"]
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="pnl_source"):
            load_mapping(path)

    def test_mapping_is_frozen(self, mapping: ColumnMapping) -> None:
        with pytest.raises((AttributeError, TypeError, ValueError)):
            mapping.source = "other"  # type: ignore[misc]

    def test_required_fields_match_the_contract(self) -> None:
        assert set(REQUIRED_FIELDS) == {
            "trade_id",
            "symbol",
            "side",
            "qty",
            "entry_ts",
            "exit_ts",
            "entry_px",
            "exit_px",
            "fees",
        }

    def test_missing_mapping_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="not found"):
            load_mapping(tmp_path / "absent.yaml")

    def test_malformed_mapping_yaml_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.yaml"
        path.write_text("columns: [unclosed\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="not valid YAML"):
            load_mapping(path)

    def test_non_mapping_mapping_document_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="must be a mapping"):
            load_mapping(path)


class TestHappyPath:
    def test_imports_the_fixture(self, mapping: ColumnMapping, symbology: SymbologyMap) -> None:
        result = read_trade_log_csv(TRADES_CSV, mapping, symbology)
        assert result.n_rows_read == result.log.n_trades == 12
        assert result.currency == "USD"
        assert result.calendars == ("WEEKDAYS_UTC",)
        assert result.warnings == ()

    def test_symbols_are_canonicalised(
        self, mapping: ColumnMapping, symbology: SymbologyMap
    ) -> None:
        log = read_trade_log_csv(TRADES_CSV, mapping, symbology).log
        assert set(np.asarray(log.symbol).tolist()) == {"ES", "NQ"}

    def test_multiplier_comes_from_the_map_not_the_file(
        self, mapping: ColumnMapping, symbology: SymbologyMap
    ) -> None:
        log = read_trade_log_csv(TRADES_CSV, mapping, symbology).log
        symbols = np.asarray(log.symbol)
        multiplier = np.asarray(log.multiplier)
        assert set(multiplier[symbols == "ES"]) == {50.0}
        assert set(multiplier[symbols == "NQ"]) == {20.0}

    def test_records_are_ordered_by_exit(
        self, mapping: ColumnMapping, symbology: SymbologyMap
    ) -> None:
        log = read_trade_log_csv(TRADES_CSV, mapping, symbology).log
        assert bool(np.all(np.diff(np.asarray(log.exit_ns)) >= 0))

    def test_declared_timezone_is_applied_not_assumed_utc(
        self, mapping: ColumnMapping, symbology: SymbologyMap
    ) -> None:
        """09:45 in New York on 2 January is 14:45 UTC, not 09:45 UTC."""
        log = read_trade_log_csv(TRADES_CSV, mapping, symbology).log
        first = np.asarray(log.entry_ns).min()
        stamp = datetime.fromtimestamp(int(first) / 1e9, tz=UTC)
        assert (stamp.hour, stamp.minute) == (14, 45)

    def test_sides_are_parsed_into_the_int_enum(
        self, mapping: ColumnMapping, symbology: SymbologyMap
    ) -> None:
        log = read_trade_log_csv(TRADES_CSV, mapping, symbology).log
        assert set(np.asarray(log.side).tolist()) == {int(Side.LONG), int(Side.SHORT)}

    def test_tags_follow_the_sort(self, mapping: ColumnMapping, symbology: SymbologyMap) -> None:
        log = read_trade_log_csv(TRADES_CSV, mapping, symbology).log
        assert len(log.tags) == log.n_trades
        assert set(log.tags[0]) == {"setup"}

    def test_negated_fee_convention_flips_the_sign(
        self, tmp_path: Path, symbology: SymbologyMap
    ) -> None:
        source = TRADES_CSV.read_text(encoding="utf-8").splitlines()
        header = source[0].split(",")
        fee_index = header.index("commission")
        rows = [source[0]]
        for line in source[1:]:
            cells = line.split(",")
            cells[fee_index] = str(-float(cells[fee_index]))
            rows.append(",".join(cells))
        csv_path = tmp_path / "negated.csv"
        csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        path = write_yaml(tmp_path / "m.yaml", mapping_payload(fee_convention="NEGATED"))
        log = read_trade_log_csv(csv_path, load_mapping(path), symbology).log
        assert bool(np.all(np.asarray(log.fees) >= 0.0))

    def test_derived_pnl_warns_that_the_identity_is_vacuous(
        self, tmp_path: Path, symbology: SymbologyMap
    ) -> None:
        payload = mapping_payload(pnl_source="DERIVE")
        del payload["columns"]["pnl"]
        path = write_yaml(tmp_path / "m.yaml", payload)
        result = read_trade_log_csv(TRADES_CSV, load_mapping(path), symbology)
        assert any("no independent verification" in w for w in result.warnings)
        assert result.log.n_trades == 12


class TestTypedFailures:
    def test_wrong_fee_sign_is_caught_loudly_by_the_non_negativity_invariant(
        self, tmp_path: Path, symbology: SymbologyMap
    ) -> None:
        """Declaring NEGATED on a file of magnitudes fails the whole file at once.

        This is the reassuring half. The fee *sign* cannot hide, because the
        non negativity invariant of ``01`` runs before the coherence identity
        and does not depend on any tolerance. The convention that hides is
        net versus gross, pinned in the next test.
        """
        path = write_yaml(tmp_path / "m.yaml", mapping_payload(fee_convention="NEGATED"))
        with pytest.raises(TradeIntegrityError, match="non negative magnitude") as excinfo:
            read_trade_log_csv(TRADES_CSV, load_mapping(path), symbology)
        assert "12 trades" in str(excinfo.value)

    def test_net_versus_gross_is_the_blind_spot_and_the_test_says_so(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        """The blind spot, asserted explicitly rather than left as a missing test.

        A file reporting P&L *before* costs, imported as if it were net, leaves
        a residual of exactly one fee per trade. On ES a round turn of 4.20 is
        far below the one tick absolute tolerance of 12.50 per contract, so the
        import succeeds and every trade is overstated by its full cost. Raising
        the fee above one tick makes it detectable, which locates the floor
        precisely: the check sees the error only when the cost of trading
        exceeds the price resolution of the instrument.
        """
        lines = TRADES_CSV.read_text(encoding="utf-8").splitlines()
        header = lines[0].split(",")
        fee_index, pnl_index = header.index("commission"), header.index("net_pnl")

        def rewrite(fee: float) -> Path:
            rows = [lines[0]]
            for line in lines[1:]:
                cells = line.split(",")
                gross = float(cells[pnl_index]) + float(cells[fee_index])
                cells[fee_index] = str(fee)
                cells[pnl_index] = str(gross)
                rows.append(",".join(cells))
            out = tmp_path / f"gross_{fee:g}.csv"
            out.write_text("\n".join(rows) + "\n", encoding="utf-8")
            return out

        quiet = rewrite(4.20)
        read_trade_log_csv(quiet, mapping, symbology)

        loud = rewrite(500.0)
        with pytest.raises(TradeIntegrityError, match="coherence identity"):
            read_trade_log_csv(loud, mapping, symbology)

    def test_declaring_gross_recovers_the_net_pnl(
        self, tmp_path: Path, symbology: SymbologyMap
    ) -> None:
        lines = TRADES_CSV.read_text(encoding="utf-8").splitlines()
        header = lines[0].split(",")
        fee_index, pnl_index = header.index("commission"), header.index("net_pnl")
        rows = [lines[0]]
        expected = []
        for line in lines[1:]:
            cells = line.split(",")
            expected.append(float(cells[pnl_index]))
            cells[pnl_index] = str(float(cells[pnl_index]) + float(cells[fee_index]))
            rows.append(",".join(cells))
        csv_path = tmp_path / "gross.csv"
        csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        path = write_yaml(tmp_path / "m.yaml", mapping_payload(pnl_convention="GROSS"))
        log = read_trade_log_csv(csv_path, load_mapping(path), symbology).log
        assert float(np.asarray(log.pnl).sum()) == pytest.approx(sum(expected), rel=1e-9)

    def test_pnl_convention_has_no_default(self, tmp_path: Path) -> None:
        payload = mapping_payload()
        del payload["pnl_convention"]
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="pnl_convention"):
            load_mapping(path)

    def test_naive_timestamps_without_a_declared_zone_are_refused(
        self, tmp_path: Path, symbology: SymbologyMap
    ) -> None:
        payload = mapping_payload()
        payload["timezone"] = None
        path = write_yaml(tmp_path / "m.yaml", payload)
        with pytest.raises(SchemaError, match="declares no timezone"):
            read_trade_log_csv(TRADES_CSV, load_mapping(path), symbology)

    def test_unknown_side_token_is_refused(self, tmp_path: Path, symbology: SymbologyMap) -> None:
        source = TRADES_CSV.read_text(encoding="utf-8").replace("Long", "Bought")
        csv_path = tmp_path / "sides.csv"
        csv_path.write_text(source, encoding="utf-8")
        path = write_yaml(tmp_path / "m.yaml", mapping_payload())
        with pytest.raises(SchemaError, match="unrecognised side token"):
            read_trade_log_csv(csv_path, load_mapping(path), symbology)

    def test_missing_column_is_refused_and_lists_the_file(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        source = TRADES_CSV.read_text(encoding="utf-8").replace("close_price", "exit_price")
        csv_path = tmp_path / "renamed.csv"
        csv_path.write_text(source, encoding="utf-8")
        with pytest.raises(SchemaError, match="missing columns") as excinfo:
            read_trade_log_csv(csv_path, mapping, symbology)
        assert "exit_price" in str(excinfo.value)

    def test_non_numeric_value_is_refused_rather_than_coerced(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        lines = TRADES_CSV.read_text(encoding="utf-8").splitlines()
        header = lines[0].split(",")
        index = header.index("quantity")
        cells = lines[1].split(",")
        cells[index] = "n/a"
        lines[1] = ",".join(cells)
        csv_path = tmp_path / "dirty.csv"
        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="non numeric"):
            read_trade_log_csv(csv_path, mapping, symbology)

    def test_unparseable_timestamp_is_refused(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        source = TRADES_CSV.read_text(encoding="utf-8").replace(
            "2024-01-02 09:45:00", "02/01/2024 09:45"
        )
        csv_path = tmp_path / "dates.csv"
        csv_path.write_text(source, encoding="utf-8")
        with pytest.raises(SchemaError, match=r"does not parse|unparseable"):
            read_trade_log_csv(csv_path, mapping, symbology)

    def test_blank_timestamp_cell_is_refused_not_dropped(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        """A truncated export leaves empty cells, which parse to NaT rather than raising."""
        lines = TRADES_CSV.read_text(encoding="utf-8").splitlines()
        header = lines[0].split(",")
        index = header.index("closed_at")
        cells = lines[3].split(",")
        cells[index] = ""
        lines[3] = ",".join(cells)
        csv_path = tmp_path / "truncated.csv"
        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="unparseable timestamps"):
            read_trade_log_csv(csv_path, mapping, symbology)

    def test_symbol_outside_the_map_is_refused(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        source = TRADES_CSV.read_text(encoding="utf-8").replace("NQZ4", "CLZ4")
        csv_path = tmp_path / "unknown.csv"
        csv_path.write_text(source, encoding="utf-8")
        with pytest.raises(SchemaError, match="not in the symbology map"):
            read_trade_log_csv(csv_path, mapping, symbology)

    def test_mixed_currency_log_is_refused(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        source = TRADES_CSV.read_text(encoding="utf-8").replace("NQZ4", "FDAXZ4")
        csv_path = tmp_path / "fx.csv"
        csv_path.write_text(source, encoding="utf-8")
        with pytest.raises(SchemaError, match="mixes currencies"):
            read_trade_log_csv(csv_path, mapping, symbology)

    def test_missing_csv_is_refused(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        with pytest.raises(SchemaError, match="not found"):
            read_trade_log_csv(tmp_path / "absent.csv", mapping, symbology)

    def test_header_only_csv_is_refused(
        self, tmp_path: Path, symbology: SymbologyMap, mapping: ColumnMapping
    ) -> None:
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text(TRADES_CSV.read_text(encoding="utf-8").splitlines()[0] + "\n")
        with pytest.raises(SchemaError, match="no rows"):
            read_trade_log_csv(csv_path, mapping, symbology)

    def test_wrong_multiplier_in_the_map_fails_the_whole_file(
        self, tmp_path: Path, mapping: ColumnMapping
    ) -> None:
        """The diagnostic D007 predicts: a bad multiplier fails every row, not one."""
        payload = yaml.safe_load(SYMBOLOGY_YAML.read_text(encoding="utf-8"))
        payload["symbols"]["ES"]["multiplier"] = 5.0
        path = write_yaml(tmp_path / "s.yaml", payload)
        with pytest.raises(TradeIntegrityError, match="coherence identity") as excinfo:
            read_trade_log_csv(TRADES_CSV, mapping, load_symbology(path))
        assert "wrong multiplier" in str(excinfo.value)

    def test_missing_tag_column_is_refused(self, tmp_path: Path, symbology: SymbologyMap) -> None:
        path = write_yaml(tmp_path / "m.yaml", mapping_payload(tag_columns=["regime"]))
        with pytest.raises(SchemaError, match="missing columns"):
            read_trade_log_csv(TRADES_CSV, load_mapping(path), symbology)


class TestEndToEnd:
    def test_csv_to_period_metrics_in_one_path(
        self, mapping: ColumnMapping, symbology: SymbologyMap
    ) -> None:
        """The v0.1 criterion: a file on disk becomes a judged strategy."""
        from datetime import timedelta

        from qvalid.adapters.calendars import weekdays_utc
        from qvalid.contracts import Basis, Period
        from qvalid.core.gridding import select_grid
        from qvalid.core.metrics import period_metrics

        result = read_trade_log_csv(TRADES_CSV, mapping, symbology)
        first = datetime.fromtimestamp(int(np.asarray(result.log.exit_ns).min()) / 1e9, tz=UTC)
        last = datetime.fromtimestamp(int(np.asarray(result.log.exit_ns).max()) / 1e9, tz=UTC)
        calendar = weekdays_utc(first - timedelta(days=7), last + timedelta(days=7))
        selection = select_grid(
            result.log,
            calendar,
            basis=Basis.FIXED_INITIAL,
            initial_capital=50_000.0,
            forced_period=Period.DAILY,
        )
        metrics = period_metrics(selection.returns, risk_free_rate=0.045)
        assert metrics.calendar_id == calendar.calendar_id
        assert metrics.n_periods >= result.log.n_trades
        assert metrics.sharpe.risk_free_rate_annual == 0.045
        assert any("MIN_PERIODS" in w for w in metrics.warnings)
