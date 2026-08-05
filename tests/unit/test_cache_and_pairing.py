"""Tests for the local cache, the provenance manifest, and two row pairing.

``TestTheCacheAvoidsTheSecondDownload`` is the acceptance criterion of ``05``
v0.7, and it is provable offline precisely because the network sits behind an
injectable protocol: the test passes a fetcher that counts its calls.

``TestTwoRoutesAgree`` is the test of the bet made in D016. The same trades,
exported one row per trade and two rows per trade, must produce byte identical
contracts. If they did not, the declarative mapping would be buying less than it
claims.

No test in this file touches the network, per ``04``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from qvalid.adapters.cache import CacheKey, LocalCache
from qvalid.adapters.symbology import load_symbology
from qvalid.adapters.tradelog import RowLayout, load_mapping, read_trade_log_csv
from qvalid.exceptions import SchemaError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PAYLOAD = b"date,value\n2024-01-02,1.0\n2024-01-03,1.1\n"
STAMP = "2026-08-04T21:00:00Z"


class CountingFetcher:
    """A fetcher that records how many times it was asked to do work."""

    def __init__(self, payload: bytes = PAYLOAD, cost: float = 0.25) -> None:
        self.calls = 0
        self._payload = payload
        self._cost = cost

    def fetch(self, key: CacheKey) -> bytes:
        self.calls += 1
        return self._payload

    @property
    def estimated_cost(self) -> float:
        return self._cost


def key(symbol: str = "DGS10", start: str = "2024-01-01") -> CacheKey:
    return CacheKey(source="fred", symbol=symbol, start=start, end="2024-12-31")


class TestTheCacheAvoidsTheSecondDownload:
    """The criterion of ``05`` v0.7, proved without a network."""

    def test_the_fetcher_is_called_once_for_two_requests(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        fetcher = CountingFetcher()
        first = cache.get(key(), fetcher, recorded_at=STAMP)
        second = cache.get(key(), fetcher, recorded_at=STAMP)
        assert fetcher.calls == 1
        assert first.downloaded is True
        assert second.downloaded is False
        assert first.payload == second.payload == PAYLOAD

    def test_a_different_slice_is_a_different_fetch(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        fetcher = CountingFetcher()
        cache.get(key(), fetcher, recorded_at=STAMP)
        cache.get(key(start="2023-01-01"), fetcher, recorded_at=STAMP)
        cache.get(key(symbol="DGS2"), fetcher, recorded_at=STAMP)
        assert fetcher.calls == 3
        assert cache.downloads() == 3

    def test_a_second_cache_over_the_same_directory_still_hits(self, tmp_path: Path) -> None:
        """The cache is on disk, not in memory: a new process sees the same slices."""
        fetcher = CountingFetcher()
        LocalCache(tmp_path).get(key(), fetcher, recorded_at=STAMP)
        LocalCache(tmp_path).get(key(), fetcher, recorded_at=STAMP)
        assert fetcher.calls == 1

    def test_the_cost_is_only_charged_on_an_actual_download(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        fetcher = CountingFetcher(cost=12.5)
        cache.get(key(), fetcher, recorded_at=STAMP)
        cache.get(key(), fetcher, recorded_at=STAMP)
        assert cache.total_cost() == 12.5


class TestTheManifest:
    def test_every_event_is_recorded_including_the_hits(self, tmp_path: Path) -> None:
        """``03``: the log answers how often a slice was used, not only when it was fetched."""
        cache = LocalCache(tmp_path)
        fetcher = CountingFetcher()
        for _ in range(3):
            cache.get(key(), fetcher, recorded_at=STAMP)
        entries = cache.manifest()
        assert len(entries) == 3
        assert [entry["downloaded"] for entry in entries] == [True, False, False]

    def test_the_manifest_carries_every_field_03_requires(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        cache.get(key(), CountingFetcher(), recorded_at=STAMP)
        entry = cache.manifest()[0]
        for field in (
            "source",
            "symbol",
            "schema",
            "start",
            "end",
            "recorded_at",
            "n_rows",
            "sha256",
            "estimated_cost",
        ):
            assert field in entry, field
        assert entry["n_rows"] == 3
        assert entry["recorded_at"] == STAMP

    def test_the_manifest_is_append_only(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        fetcher = CountingFetcher()
        cache.get(key(), fetcher, recorded_at=STAMP)
        first = cache.manifest_path.read_text(encoding="utf-8")
        cache.get(key(symbol="DGS2"), fetcher, recorded_at=STAMP)
        assert cache.manifest_path.read_text(encoding="utf-8").startswith(first)

    def test_each_line_is_valid_json_with_sorted_keys(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        cache.get(key(), CountingFetcher(), recorded_at=STAMP)
        line = cache.manifest_path.read_text(encoding="utf-8").splitlines()[0]
        parsed = json.loads(line)
        assert list(parsed) == sorted(parsed)

    def test_a_corrupt_manifest_line_raises_rather_than_being_skipped(self, tmp_path: Path) -> None:
        """A provenance log with a hole reads as complete, which is worse than none."""
        cache = LocalCache(tmp_path)
        cache.get(key(), CountingFetcher(), recorded_at=STAMP)
        with cache.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        with pytest.raises(SchemaError, match="not valid JSON"):
            cache.manifest()

    def test_blank_lines_in_the_manifest_are_tolerated(self, tmp_path: Path) -> None:
        """A trailing newline from an editor is not a hole in the log."""
        cache = LocalCache(tmp_path)
        cache.get(key(), CountingFetcher(), recorded_at=STAMP)
        with cache.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write("\n   \n")
        assert len(cache.manifest()) == 1

    def test_an_empty_manifest_reads_as_empty(self, tmp_path: Path) -> None:
        assert LocalCache(tmp_path).manifest() == []
        assert LocalCache(tmp_path).total_cost() == 0.0


class TestImmutability:
    def test_an_edited_raw_file_is_detected(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        result = cache.get(key(), CountingFetcher(), recorded_at=STAMP)
        assert cache.verify() == {}
        result.path.write_bytes(b"tampered\n")
        problems = cache.verify()
        assert len(problems) == 1
        assert "immutable" in next(iter(problems.values()))

    def test_a_missing_raw_file_is_detected(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path)
        result = cache.get(key(), CountingFetcher(), recorded_at=STAMP)
        result.path.unlink()
        assert "missing file" in next(iter(cache.verify().values()))

    def test_an_empty_payload_is_refused_rather_than_cached(self, tmp_path: Path) -> None:
        """An empty slice cached once would be served for ever."""
        cache = LocalCache(tmp_path)
        with pytest.raises(SchemaError, match="empty payload"):
            cache.get(key(), CountingFetcher(payload=b""), recorded_at=STAMP)
        assert not cache.contains(key())

    def test_the_key_digest_changes_with_every_field(self) -> None:
        base = CacheKey(source="a", symbol="b", start="c", end="d", schema="e")
        variants = [
            CacheKey(source="z", symbol="b", start="c", end="d", schema="e"),
            CacheKey(source="a", symbol="z", start="c", end="d", schema="e"),
            CacheKey(source="a", symbol="b", start="z", end="d", schema="e"),
            CacheKey(source="a", symbol="b", start="c", end="z", schema="e"),
            CacheKey(source="a", symbol="b", start="c", end="d", schema="z"),
        ]
        digests = {base.digest()} | {variant.digest() for variant in variants}
        assert len(digests) == 6

    def test_the_separator_keeps_fields_from_blurring(self) -> None:
        """``a|b`` and ``ab|`` must not hash to the same key."""
        assert CacheKey("a", "b", "x", "y").digest() != CacheKey("ab", "", "x", "y").digest()

    def test_the_directories_of_03_are_created(self, tmp_path: Path) -> None:
        cache = LocalCache(tmp_path / "data")
        assert (cache.root / "raw").is_dir()
        assert (cache.root / "curated").is_dir()


class TestTwoRoutesAgree:
    """The test of D016: shape needs code, names need only a file."""

    @pytest.fixture
    def symbology(self):
        return load_symbology(FIXTURES / "symbology.yaml")

    def test_the_paired_export_produces_the_same_contract(self, symbology) -> None:
        one_row = read_trade_log_csv(
            FIXTURES / "trades_generic.csv",
            load_mapping(FIXTURES / "mapping_generic.yaml"),
            symbology,
        )
        two_row = read_trade_log_csv(
            FIXTURES / "trades_paired.csv",
            load_mapping(FIXTURES / "mapping_tradingview.yaml"),
            symbology,
        )
        assert one_row.log.n_trades == two_row.log.n_trades
        for field in (
            "trade_id",
            "symbol",
            "side",
            "qty",
            "multiplier",
            "entry_ns",
            "exit_ns",
            "entry_px",
            "exit_px",
            "fees",
            "pnl",
        ):
            left = np.asarray(getattr(one_row.log, field))
            right = np.asarray(getattr(two_row.log, field))
            if left.dtype.kind in "US":
                np.testing.assert_array_equal(left, right, err_msg=field)
            else:
                np.testing.assert_allclose(left, right, err_msg=field)

    def test_the_side_comes_from_the_entry_leg(self, symbology) -> None:
        """The exit of a long is a sell; reading direction off the exit inverts everything."""
        mapping = load_mapping(FIXTURES / "mapping_tradingview.yaml")
        assert mapping.row_layout is RowLayout.TWO_ROWS_PER_TRADE
        log = read_trade_log_csv(FIXTURES / "trades_paired.csv", mapping, symbology).log
        reference = read_trade_log_csv(
            FIXTURES / "trades_generic.csv",
            load_mapping(FIXTURES / "mapping_generic.yaml"),
            symbology,
        ).log
        np.testing.assert_array_equal(np.asarray(log.side), np.asarray(reference.side))


class TestPairingFailures:
    @pytest.fixture
    def symbology(self):
        return load_symbology(FIXTURES / "symbology.yaml")

    def _mapping_payload(self, **overrides):
        payload = yaml.safe_load(
            (FIXTURES / "mapping_tradingview.yaml").read_text(encoding="utf-8")
        )
        payload.update(overrides)
        return payload

    def _write(self, tmp_path: Path, payload) -> Path:
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    def test_a_half_paired_trade_is_refused(self, tmp_path: Path, symbology) -> None:
        """Dropping it would change every statistic while the import still looked clean."""
        lines = (FIXTURES / "trades_paired.csv").read_text(encoding="utf-8").splitlines()
        broken = tmp_path / "half.csv"
        broken.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="exactly two legs"):
            read_trade_log_csv(
                broken, load_mapping(FIXTURES / "mapping_tradingview.yaml"), symbology
            )

    def test_two_entry_legs_are_refused(self, tmp_path: Path, symbology) -> None:
        """Two rows is not the same as one entry and one exit."""
        lines = (FIXTURES / "trades_paired.csv").read_text(encoding="utf-8").splitlines()
        header, body = lines[0], lines[1:]
        body[1] = body[1].replace("Exit Long", "Entry Long").replace("Exit Short", "Entry Short")
        broken = tmp_path / "two_entries.csv"
        broken.write_text("\n".join([header, *body]) + "\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="exactly one entry leg and one exit leg"):
            read_trade_log_csv(
                broken, load_mapping(FIXTURES / "mapping_tradingview.yaml"), symbology
            )

    def test_an_unknown_leg_marker_is_refused(self, tmp_path: Path, symbology) -> None:
        source = (FIXTURES / "trades_paired.csv").read_text(encoding="utf-8")
        broken = tmp_path / "markers.csv"
        broken.write_text(source.replace("Entry Long", "Open Long"), encoding="utf-8")
        with pytest.raises(SchemaError, match="unrecognised leg marker"):
            read_trade_log_csv(
                broken, load_mapping(FIXTURES / "mapping_tradingview.yaml"), symbology
            )

    def test_a_missing_pairing_column_is_refused(self, tmp_path: Path, symbology) -> None:
        payload = self._mapping_payload(price_column="absent")
        with pytest.raises(SchemaError, match="the pairing needs column"):
            read_trade_log_csv(
                FIXTURES / "trades_paired.csv",
                load_mapping(self._write(tmp_path, payload)),
                symbology,
            )

    def test_the_layout_demands_its_own_fields(self, tmp_path: Path) -> None:
        payload = self._mapping_payload()
        del payload["pair_key_column"]
        with pytest.raises(SchemaError, match="TWO_ROWS_PER_TRADE requires"):
            load_mapping(self._write(tmp_path, payload))

    def test_markers_may_not_overlap(self, tmp_path: Path) -> None:
        payload = self._mapping_payload(entry_markers=["Entry Long"], exit_markers=["entry long"])
        with pytest.raises(SchemaError, match="claimed by both legs"):
            load_mapping(self._write(tmp_path, payload))

    def test_markers_are_mandatory(self, tmp_path: Path) -> None:
        payload = self._mapping_payload(entry_markers=[])
        with pytest.raises(SchemaError, match="entry_markers and exit_markers"):
            load_mapping(self._write(tmp_path, payload))

    def test_the_one_row_layout_still_demands_its_leg_fields(self, tmp_path: Path) -> None:
        payload = yaml.safe_load((FIXTURES / "mapping_generic.yaml").read_text(encoding="utf-8"))
        del payload["columns"]["entry_px"]
        with pytest.raises(SchemaError, match="required fields"):
            load_mapping(self._write(tmp_path, payload))

    def test_the_two_row_layout_does_not_demand_them(self, tmp_path: Path) -> None:
        """Binding a leg field would be binding something the source does not have."""
        mapping = load_mapping(FIXTURES / "mapping_tradingview.yaml")
        assert "entry_px" not in mapping.columns
        assert "entry_ts" not in mapping.columns
        assert set(mapping.required_fields) < set(
            load_mapping(FIXTURES / "mapping_generic.yaml").required_fields
        )
