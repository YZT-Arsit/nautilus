"""Tests for the Hive-partitioned Parquet bar source (data_engine)."""
from __future__ import annotations

from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
ds = pytest.importorskip("pyarrow.dataset")

from data_engine import load_events
from data_engine.events import BarEvent
from data_engine.sources.parquet_bars import (
    ParquetBarSource,
    load_parquet_bars,
    resolve_bar_timestamp_column,
)
from data_engine.time import ONE_SECOND_NS, to_event_time_ns


def _write_dataset(root: Path, columns: dict, partitioning: list[str]) -> Path:
    """Write a Hive-partitioned Parquet dataset and return its root."""
    table = pa.table(columns)
    ds.write_dataset(
        table,
        str(root),
        format="parquet",
        partitioning=partitioning,
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
    )
    return root


def _ohlcv_dataset(root: Path) -> Path:
    """Two trading dates x two instruments, 4 bars each, full OHLCV."""
    n_dates, instruments = ("2024-01-01", "2024-01-02"), ("BTC-USDT", "ETH-USDT")
    cols = {k: [] for k in
            ("event_time_ns", "open", "high", "low", "close", "volume",
             "trading_date", "instrument_id")}
    for date in n_dates:
        for inst in instruments:
            for i in range(4):
                cols["event_time_ns"].append(i * ONE_SECOND_NS)
                cols["open"].append(100.0 + i)
                cols["high"].append(101.0 + i)
                cols["low"].append(99.0 + i)
                cols["close"].append(100.5 + i)
                cols["volume"].append(10.0 + i)
                cols["trading_date"].append(date)
                cols["instrument_id"].append(inst)
    return _write_dataset(root, cols, ["trading_date", "instrument_id"])


# A. Basic load / split ------------------------------------------------------

class TestBasicLoad:

    def test_warmup_live_split_and_sorted(self, tmp_path):
        root = _ohlcv_dataset(tmp_path / "bars")
        # Filter to a single partition (4 bars), warmup 2 -> live 2.
        src = ParquetBarSource(
            root=str(root), instrument_id="BTC-USDT", warmup_bars=2,
            filters={"trading_date": "2024-01-01", "instrument_id": "BTC-USDT"},
        )
        warmup, live = src.warmup(), src.stream()
        assert len(warmup) == 2 and len(live) == 2
        times = [b.event_time_ns for b in warmup + live]
        assert times == sorted(times)  # sorted once after load
        assert warmup[0].close == 100.5

    def test_full_ohlcv_fields(self, tmp_path):
        root = _ohlcv_dataset(tmp_path / "bars")
        src = ParquetBarSource(
            root=str(root), instrument_id="BTC-USDT", warmup_bars=0,
            filters={"trading_date": "2024-01-01", "instrument_id": "BTC-USDT"},
        )
        bar = src.stream()[0]
        assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100.0, 101.0, 99.0, 100.5, 10.0)


# B. Filters / partition pruning ---------------------------------------------

class TestFilters:

    def test_filter_selects_one_partition(self, tmp_path):
        root = _ohlcv_dataset(tmp_path / "bars")
        # 2 dates x 2 instruments x 4 = 16 rows total; one (date, inst) = 4 rows.
        all_bars = ParquetBarSource(root=str(root), instrument_id="X").stream()
        assert len(all_bars) == 16
        one = ParquetBarSource(
            root=str(root), instrument_id="ETH-USDT",
            filters={"trading_date": "2024-01-02", "instrument_id": "ETH-USDT"},
        ).stream()
        assert len(one) == 4


# C. Defaults / timestamps ---------------------------------------------------

class TestDefaultsAndTimestamps:

    def test_missing_optional_fields_default_to_close(self, tmp_path):
        root = _write_dataset(
            tmp_path / "minimal",
            {"event_time_ns": [0, ONE_SECOND_NS], "close": [100.0, 101.0],
             "trading_date": ["2024-01-01", "2024-01-01"]},
            ["trading_date"],
        )
        bar = ParquetBarSource(root=str(root), instrument_id="X", warmup_bars=0).stream()[0]
        assert bar.open == bar.high == bar.low == 100.0
        assert bar.volume == 0.0

    def test_timestamp_unit_conversion(self, tmp_path):
        root = _write_dataset(
            tmp_path / "ms",
            {"ts": [5, 6], "close": [100.0, 101.0], "trading_date": ["d", "d"]},
            ["trading_date"],
        )
        bars = ParquetBarSource(
            root=str(root), instrument_id="X", warmup_bars=0,
            timestamp_column="ts", timestamp_unit="ms",
        ).stream()
        assert [b.event_time_ns for b in bars] == [5 * 1_000_000, 6 * 1_000_000]

    def test_missing_timestamp_column_generates_monotonic(self, tmp_path):
        root = _write_dataset(
            tmp_path / "nots",
            {"close": [100.0, 101.0, 102.0], "trading_date": ["d", "d", "d"]},
            ["trading_date"],
        )
        bars = ParquetBarSource(
            root=str(root), instrument_id="X", warmup_bars=0,
            timestamp_column="event_time_ns",  # absent -> monotonic fallback
        ).stream()
        assert [b.event_time_ns for b in bars] == [0, ONE_SECOND_NS, 2 * ONE_SECOND_NS]


# D. Error handling ----------------------------------------------------------

class TestErrors:

    def test_missing_close_column_raises(self, tmp_path):
        root = _write_dataset(
            tmp_path / "noclose",
            {"event_time_ns": [0, 1], "price": [100.0, 101.0], "trading_date": ["d", "d"]},
            ["trading_date"],
        )
        with pytest.raises(ValueError, match="close column"):
            ParquetBarSource(root=str(root), instrument_id="X").stream()

    def test_null_close_raises(self, tmp_path):
        root = _write_dataset(
            tmp_path / "nullclose",
            {"event_time_ns": [0, 1], "close": [100.0, None], "trading_date": ["d", "d"]},
            ["trading_date"],
        )
        with pytest.raises(ValueError, match="close"):
            ParquetBarSource(root=str(root), instrument_id="X").stream()

    def test_unsupported_unit_raises_in_init(self):
        with pytest.raises(ValueError, match="unsupported timestamp_unit"):
            ParquetBarSource(root="x", instrument_id="X", timestamp_unit="weeks")

    def test_missing_root_raises(self):
        with pytest.raises(ValueError, match="requires a 'root'"):
            load_parquet_bars({"mode": "parquet_bars"})


# E. Loader integration / aliases --------------------------------------------

class TestLoaderAliases:

    @pytest.mark.parametrize("mode", ["parquet_bars", "hive_parquet_bars"])
    def test_both_aliases_work(self, tmp_path, mode):
        root = _ohlcv_dataset(tmp_path / "bars")
        warmup, live = load_events({
            "mode": mode,
            "root": str(root),
            "instrument_id": "BTC-USDT",
            "warmup_bars": 2,
            "filters": {"trading_date": "2024-01-01", "instrument_id": "BTC-USDT"},
        })
        assert len(warmup) == 2 and len(list(live)) == 2


# E2. Mixed unified root (bars + trades under one market_data root) -----------

def _write_mixed_market_data_root(root: Path) -> tuple[int, int]:
    """Write both a bar (bar_type=5m) and a trade (data_type=aggTrades) partition
    under one root, mirroring the real unified ``market_data`` layout.  The trade
    partition has price/quantity/side but NO ``close`` column."""
    base = 1_700_000_000 * ONE_SECOND_NS
    n_bars, n_trades = 5, 8
    bar_tbl = pa.table({
        "ts": [base + i * ONE_SECOND_NS for i in range(n_bars)],
        "open": [100.0 + i for i in range(n_bars)],
        "high": [101.0 + i for i in range(n_bars)],
        "low": [99.0 + i for i in range(n_bars)],
        "close": [100.5 + i for i in range(n_bars)],
        "volume": [10.0 + i for i in range(n_bars)],
        "exchange": ["BINANCE"] * n_bars, "venue_type": ["spot"] * n_bars,
        "symbol": ["BTCUSDT"] * n_bars, "bar_type": ["5m"] * n_bars,
        "date": ["2024-06-01"] * n_bars,
    })
    ds.write_dataset(
        bar_tbl, str(root), format="parquet",
        partitioning=["exchange", "venue_type", "symbol", "bar_type", "date"],
        partitioning_flavor="hive", existing_data_behavior="overwrite_or_ignore",
    )
    trade_tbl = pa.table({
        "ts": [base + i * ONE_SECOND_NS for i in range(n_trades)],
        "price": [200.0 + i for i in range(n_trades)],   # NOTE: no 'close' column
        "quantity": [1.0 for _ in range(n_trades)],
        "side": ["BUY" for _ in range(n_trades)],
        "exchange": ["BINANCE"] * n_trades, "venue_type": ["spot"] * n_trades,
        "symbol": ["BTCUSDT"] * n_trades, "data_type": ["aggTrades"] * n_trades,
        "date": ["2024-06-01"] * n_trades,
    })
    ds.write_dataset(
        trade_tbl, str(root), format="parquet",
        partitioning=["exchange", "venue_type", "symbol", "data_type", "date"],
        partitioning_flavor="hive", existing_data_behavior="overwrite_or_ignore",
    )
    return n_bars, n_trades


class TestMixedRoot:
    """Regression: bar loader must ignore the data_type=aggTrades partition under a
    unified root and never hit 'required close column close is missing'."""

    def test_bar_loader_ignores_trade_partition(self, tmp_path):
        root = tmp_path / "market_data"
        n_bars, _ = _write_mixed_market_data_root(root)
        warmup, live = load_events({
            "mode": "hive_parquet_bars",
            "root": str(root),
            "instrument_id": "BTCUSDT.BINANCE",
            "warmup_bars": 0,
            "timestamp_column": "ts",
            "timestamp_unit": "ns",
            "filters": {"exchange": "BINANCE", "venue_type": "spot",
                        "symbol": "BTCUSDT", "bar_type": "5m"},
        })
        live = list(live)
        # Only the bar rows load — the 8 trade rows are ignored.
        assert len(live) == n_bars
        assert all(isinstance(b, BarEvent) for b in live)
        times = [b.event_time_ns for b in live]
        assert times == sorted(times)
        assert live[0].open == 100.0
        assert live[0].close == 100.5            # bar close, NOT trade price (200.0)
        assert live[-1].close == 100.5 + (n_bars - 1)


# E3. Timestamp column resolution (Binance Vision bar parquet uses ``ts``) -----

class TestTimestampColumnResolutionPure:
    """Pure rules for :func:`resolve_bar_timestamp_column` (no pyarrow needed)."""

    def test_explicit_existing_column_respected(self):
        assert resolve_bar_timestamp_column({"ts", "close"}, "ts") == "ts"
        assert resolve_bar_timestamp_column(
            {"event_time_ns", "close"}, "event_time_ns") == "event_time_ns"

    def test_default_event_time_ns_falls_back_to_ts(self):
        # the real Binance Vision case: default config, only ``ts`` present
        assert resolve_bar_timestamp_column({"ts", "close"}, "event_time_ns") == "ts"

    def test_event_time_ns_preferred_when_both_present(self):
        assert resolve_bar_timestamp_column(
            {"event_time_ns", "ts", "close"}, "event_time_ns") == "event_time_ns"

    def test_custom_absent_column_kept_for_fallback(self):
        # a custom (non-default) column that is absent keeps existing behavior
        assert resolve_bar_timestamp_column({"close"}, "weird_ts") == "weird_ts"

    def test_no_time_columns_keeps_default(self):
        assert resolve_bar_timestamp_column({"close"}, "event_time_ns") == "event_time_ns"

    def test_none_autodetects(self):
        assert resolve_bar_timestamp_column({"ts", "close"}, None) == "ts"
        assert resolve_bar_timestamp_column({"event_time_ns", "ts"}, None) == "event_time_ns"
        assert resolve_bar_timestamp_column({"close"}, None) == "event_time_ns"


def _ts_us_bar_dataset(root: Path, *, n=5, bar_type="1m", with_event_time_ns=False) -> Path:
    """Binance-Vision-style hive bar dataset whose time column is ``ts``
    (``timestamp[us]`` datetimes), under exchange/venue_type/symbol/bar_type/date."""
    from datetime import datetime, timedelta

    base = datetime(2026, 6, 16, 0, 0)  # naive == UTC for to_event_time_ns
    ts_vals = [base + timedelta(minutes=i) for i in range(n)]
    cols = {
        "ts": pa.array(ts_vals, type=pa.timestamp("us")),
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [10.0 + i for i in range(n)],
        "exchange": ["BINANCE"] * n, "venue_type": ["spot"] * n,
        "symbol": ["BTCUSDT"] * n, "bar_type": [bar_type] * n,
        "date": ["2026-06-16"] * n,
    }
    if with_event_time_ns:
        # distinct sentinel ns so we can prove event_time_ns is *preferred*
        cols["event_time_ns"] = [(i + 1) * ONE_SECOND_NS for i in range(n)]
    ds.write_dataset(
        pa.table(cols), str(root), format="parquet",
        partitioning=["exchange", "venue_type", "symbol", "bar_type", "date"],
        partitioning_flavor="hive", existing_data_behavior="overwrite_or_ignore",
    )
    return root


class TestTimestampResolutionIntegration:
    """End-to-end: ``ts``-only bar parquet loads with real (not 1970) times."""

    def _load(self, root, *, bar_type="1m", **extra):
        cfg = {
            "mode": "hive_parquet_bars", "root": str(root),
            "instrument_id": "BTCUSDT.BINANCE", "warmup_bars": 0,
            "filters": {"exchange": "BINANCE", "venue_type": "spot",
                        "symbol": "BTCUSDT", "bar_type": bar_type},
        }
        cfg.update(extra)
        warmup, live = load_events(cfg)
        return list(warmup) + list(live)

    def test_ts_only_default_config_uses_real_timestamps(self, tmp_path):
        from datetime import datetime
        root = _ts_us_bar_dataset(tmp_path / "market_data", n=5)
        # DEFAULT config — no timestamp_column override (constructor default
        # 'event_time_ns', which is ABSENT -> must resolve to 'ts').
        bars = self._load(root)
        assert len(bars) == 5
        expected_first = to_event_time_ns(datetime(2026, 6, 16, 0, 0), "ns")
        assert bars[0].event_time_ns == expected_first        # real 2026 time
        assert bars[0].event_time_ns > 1_700_000_000 * ONE_SECOND_NS  # not 1970
        assert bars[-1].event_time_ns == to_event_time_ns(datetime(2026, 6, 16, 0, 4), "ns")
        assert [b.event_time_ns for b in bars] == sorted(b.event_time_ns for b in bars)

    def test_event_time_ns_preferred_when_present(self, tmp_path):
        root = _ts_us_bar_dataset(tmp_path / "market_data", n=5, with_event_time_ns=True)
        bars = self._load(root)
        # event_time_ns present -> used in preference to ts (sentinel 1s..5s)
        assert [b.event_time_ns for b in bars] == [(i + 1) * ONE_SECOND_NS for i in range(5)]

    def test_explicit_ts_column_respected(self, tmp_path):
        from datetime import datetime
        root = _ts_us_bar_dataset(tmp_path / "market_data", n=3)
        bars = self._load(root, timestamp_column="ts")
        assert bars[0].event_time_ns == to_event_time_ns(datetime(2026, 6, 16, 0, 0), "ns")

    def test_5m_bar_type_also_resolves_ts(self, tmp_path):
        from datetime import datetime
        root = _ts_us_bar_dataset(tmp_path / "market_data", n=4, bar_type="5m")
        bars = self._load(root, bar_type="5m")
        assert len(bars) == 4
        assert bars[0].event_time_ns == to_event_time_ns(datetime(2026, 6, 16, 0, 0), "ns")


# F. End-to-end via run_strategy ---------------------------------------------

class TestRunStrategyParquet:

    def test_run_strategy_with_parquet_config(self, tmp_path, monkeypatch, capsys):
        import run_strategy

        # 25 monotonically rising-then-falling bars in one partition.
        closes = [100.0] * 21 + [110.0, 110.0, 90.0, 90.0]
        root = _write_dataset(
            tmp_path / "bars",
            {"event_time_ns": [i * ONE_SECOND_NS for i in range(len(closes))],
             "close": closes,
             "trading_date": ["2024-01-01"] * len(closes)},
            ["trading_date"],
        )
        cfg = tmp_path / "parquet.yaml"
        cfg.write_text(
            "strategy: ma_crossover\n"
            "params: {fast_window: 5, slow_window: 20}\n"
            f"data:\n"
            f"  mode: parquet_bars\n"
            f"  root: {root}\n"
            f"  instrument_id: BTC-USDT\n"
            f"  warmup_bars: 20\n"
            f"  filters: {{trading_date: '2024-01-01'}}\n"
            "output: {print_table: false}\n"
        )
        monkeypatch.chdir(tmp_path)  # prove path independence
        run_strategy.main(["--config", str(cfg)])
        assert "[ma_crossover] warmed up" in capsys.readouterr().out
