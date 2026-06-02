"""
Tests for nautilus_ext.ccxt_live — polling paper live runner.

All 9 tests use mocked ccxt and do NOT make real network calls.
All 9 tests pass without compiled nautilus_trader Cython extensions.

Test summary
------------
1.  test_polling_feed_identifies_new_bars
        poll_once() returns only bars not seen before.
2.  test_duplicate_ohlcv_not_repeated
        bars already in _seen_ts are filtered out.
3.  test_drop_incomplete_bar_drops_last
        last bar of each poll batch is dropped when drop_incomplete_bar=True.
4.  test_warmup_bars_generated
        warmup() downloads and returns historical bars via mocked exchange.
5.  test_signal_recorder_writes_csv_parquet
        SignalRecorder.to_csv() / to_parquet() produce valid files.
6.  test_dry_run_no_real_orders
        DryRunExecutionRecorder records intent without real submission.
7.  test_paper_live_runner_stops_at_max_bars
        CcxtPaperLiveRunner.run(max_bars=N) stops after N bars.
8.  test_enable_order_submit_raises
        CcxtPollingLiveConfig(enable_order_submit=True) raises NotImplementedError.
9.  test_existing_ccxt_historical_tests_not_broken
        Importing the historical ccxt connector still works.
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from nautilus_ext.ccxt_live.polling_config import CcxtPollingLiveConfig
from nautilus_ext.ccxt_live.polling_bar_feed import CcxtPollingBarFeed
from nautilus_ext.ccxt_live.signal_recorder import SignalRecorder
from nautilus_ext.ccxt_live.dry_run_execution import DryRunExecutionRecorder
from nautilus_ext.ccxt_live.paper_live_runner import CcxtPaperLiveRunner
from nautilus_ext.strategies.signal_types import BarInput, SignalResult


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_BASE_TS = 1_704_067_200_000   # 2024-01-01T00:00:00Z in ms
_TF_MS = 60_000                # 1-minute bars


def _make_raw_rows(n: int, start_ms: int = _BASE_TS) -> list[list]:
    """Generate n synthetic OHLCV rows [ts_ms, o, h, l, c, v]."""
    return [
        [start_ms + i * _TF_MS, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000.0 + i]
        for i in range(n)
    ]


def _make_ohlcv_df(n: int, start_ms: int = _BASE_TS) -> pd.DataFrame:
    """Return a feed-style DataFrame (same schema as CcxtPollingBarFeed._rows_to_df)."""
    rows = _make_raw_rows(n, start_ms)
    df = pd.DataFrame(rows, columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    df["datetime"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df["symbol"] = "BTC/USDT:USDT"
    df["exchange"] = "binance"
    df["timeframe"] = "1m"
    return df


def _make_signal_result(entry: bool = False, exit_: bool = False) -> SignalResult:
    return SignalResult(
        entry_side="SELL" if entry else None,
        entry_order_type="stop_market" if entry else None,
        entry_price=98.0 if entry else None,
        exit_side="BUY" if exit_ else None,
        reason="enter_short" if entry else ("exit_short" if exit_ else None),
        debug={
            "current_bar": 42,
            "momentum": 0.05,
            "vwm": 1.2,
            "atr": 0.8,
            "bull_setup": False,
            "bear_setup": entry,
            "se_price": 101.0 if entry else None,
            "s_setup": 0,
            "entry_signal": entry,
            "exit_signal": exit_,
            "entry_setup_active": entry,
            "entry_trigger_price": 98.0 if entry else None,
        },
    )


def _base_config(**kwargs) -> CcxtPollingLiveConfig:
    defaults = dict(
        exchange_id="binance",
        market_type="swap",
        symbol="BTC/USDT:USDT",
        timeframe="1m",
        venue="BINANCE",
        poll_interval_seconds=60.0,
        lookback_bars=5,
    )
    defaults.update(kwargs)
    return CcxtPollingLiveConfig(**defaults)


def _mock_exchange(rows: list[list], ignore_since: bool = False):
    """Create a minimal ccxt exchange mock that returns *rows* from fetch_ohlcv.

    Parameters
    ----------
    rows : list[list]
        OHLCV rows to serve.
    ignore_since : bool
        When True, the mock returns all rows regardless of the `since` filter.
        Use this when the test rows have historical timestamps that would be
        filtered out by the live-time since_ms computed from current time.
    """
    class _Exc:
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            if ignore_since or since is None:
                filtered = rows
            else:
                filtered = [r for r in rows if r[0] >= since]
            return filtered[:limit] if limit else filtered

        def load_markets(self):
            return {}

    return _Exc()


# ---------------------------------------------------------------------------
# Helper mock classes for paper live runner
# ---------------------------------------------------------------------------

class _MockInstrumentId:
    def __str__(self) -> str:
        return "BTCUSDT-PERP.BINANCE"


class _MockInstrument:
    id = _MockInstrumentId()


class _MockSignalEngine:
    """Minimal signal engine that returns a no-op result every bar."""
    def __init__(self):
        self.calls: list[BarInput] = []

    def update(self, bar: BarInput, position: int = 0, bars_since_entry: int = 0) -> SignalResult:
        self.calls.append(bar)
        return _make_signal_result()


class _PollCountFeed:
    """Injects a fixed-row payload on each poll_once() call."""

    def __init__(self, rows_per_poll: int = 2):
        self._initialized = True
        self._rows_per_poll = rows_per_poll
        self._poll_count = 0

    def initialize(self) -> None:
        pass

    def warmup(self) -> pd.DataFrame:
        return _make_ohlcv_df(0)   # empty warmup for speed

    def poll_once(self) -> pd.DataFrame:
        start = _BASE_TS + self._poll_count * self._rows_per_poll * _TF_MS
        self._poll_count += 1
        return _make_ohlcv_df(self._rows_per_poll, start_ms=start)

    @property
    def instrument(self):
        return _MockInstrument()

    @property
    def bar_type_str(self) -> str:
        return "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCcxtPollingFeed:

    def test_polling_feed_identifies_new_bars(self):
        """poll_once() returns only unseen bars (and drops the incomplete last)."""
        config = _base_config()
        feed = CcxtPollingBarFeed(config)

        # Inject mock exchange and mark initialized (bypasses Nautilus).
        # Set _last_seen_ts = _BASE_TS - 1 so since_ms = _BASE_TS and the
        # mock's since-filter matches our 2024 test rows.
        rows = _make_raw_rows(3)   # T0, T1, T2
        feed._exchange = _mock_exchange(rows)
        feed._initialized = True
        feed._last_seen_ts = _BASE_TS - 1

        result = feed.poll_once()

        # With drop_incomplete_bar=True: 3 rows → 2 delivered (T2 dropped)
        assert len(result) == 2
        ts_list = result["timestamp_ms"].tolist()
        assert ts_list[0] == _BASE_TS
        assert ts_list[1] == _BASE_TS + _TF_MS

    def test_duplicate_ohlcv_not_repeated(self):
        """Bars already in _seen_ts must NOT appear in poll_once() output."""
        config = _base_config()
        feed = CcxtPollingBarFeed(config)

        rows = _make_raw_rows(3)   # T0, T1, T2
        # last_seen_ts controls since_ms so the mock's filter works
        feed._exchange = _mock_exchange(rows)
        feed._initialized = True
        feed._seen_ts = {r[0] for r in rows}   # all three pre-registered
        feed._last_seen_ts = rows[-1][0]        # since_ms = T2 + 1 → no rows

        result = feed.poll_once()

        assert result.empty, f"Expected empty DataFrame; got {len(result)} rows."

    def test_drop_incomplete_bar_drops_last(self):
        """The last bar in each poll batch is dropped when drop_incomplete_bar=True."""
        config = _base_config(drop_incomplete_bar=True)
        feed = CcxtPollingBarFeed(config)

        rows = _make_raw_rows(4)   # T0 … T3; T3 is the 'open' bar
        feed._exchange = _mock_exchange(rows)
        feed._initialized = True
        feed._last_seen_ts = _BASE_TS - 1   # since_ms = _BASE_TS → all rows visible

        result = feed.poll_once()

        # 4 rows: 4 new, drop last → 3 returned
        assert len(result) == 3
        last_ts = result["timestamp_ms"].iloc[-1]
        assert last_ts == _BASE_TS + 2 * _TF_MS   # T2, not T3

    def test_drop_incomplete_bar_false(self):
        """When drop_incomplete_bar=False all new bars are returned."""
        config = _base_config(drop_incomplete_bar=False)
        feed = CcxtPollingBarFeed(config)

        rows = _make_raw_rows(3)
        feed._exchange = _mock_exchange(rows)
        feed._initialized = True
        feed._last_seen_ts = _BASE_TS - 1   # since_ms = _BASE_TS

        result = feed.poll_once()

        assert len(result) == 3

    def test_warmup_bars_generated(self):
        """warmup() downloads historical bars through mocked exchange.

        config.since="2024-01-01T00:00:00Z" makes warmup compute
        since_ms = _BASE_TS, so the mock's since-filter matches our test rows.
        """
        config = _base_config(warmup_bars=5, since="2024-01-01T00:00:00Z")
        feed = CcxtPollingBarFeed(config)

        # 5 rows — ignore_since=True ensures they're returned even if CcxtOhlcvConnector
        # computes a slightly different since_ms due to ISO8601 round-trip precision.
        rows = _make_raw_rows(5)
        feed._exchange = _mock_exchange(rows, ignore_since=True)
        feed._initialized = True   # skip Nautilus instrument building

        df = feed.warmup()

        # CcxtOhlcvConnector.fetch() drops the last incomplete bar by default
        # 5 rows → 4 complete bars
        assert len(df) == 4
        assert "timestamp_ms" in df.columns
        # All warmup timestamps must be registered as seen
        assert feed._last_seen_ts is not None
        assert len(feed._seen_ts) == 4

    def test_seen_timestamps_accumulate_across_polls(self):
        """_seen_ts grows monotonically so dedup works across multiple polls."""
        config = _base_config()
        feed = CcxtPollingBarFeed(config)

        rows_a = _make_raw_rows(3, _BASE_TS)
        rows_b = _make_raw_rows(3, _BASE_TS + 3 * _TF_MS)

        feed._exchange = _mock_exchange(rows_a)
        feed._initialized = True
        feed._last_seen_ts = _BASE_TS - 1   # since_ms = _BASE_TS for first poll
        feed.poll_once()   # registers T0, T1 (T2 dropped)

        # Second poll: _last_seen_ts = T1, since_ms = T1+1; rows_b start at T3 > T1+1
        feed._exchange = _mock_exchange(rows_b)
        feed.poll_once()   # registers T3, T4 (T5 dropped)

        assert len(feed._seen_ts) == 4


class TestSignalRecorder:

    def test_signal_recorder_appends_row(self):
        rec = SignalRecorder("BTCUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        row = _make_ohlcv_df(1).iloc[0]
        result = _make_signal_result(entry=True)
        rec.append(row, result, position=-1)

        assert len(rec) == 1
        df = rec.to_dataframe()
        assert df["position"].iloc[0] == -1
        assert df["entry_signal"].iloc[0] is True or df["entry_signal"].iloc[0] == True

    def test_signal_recorder_writes_csv_parquet(self, tmp_path):
        rec = SignalRecorder("BTCUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        for i, row in _make_ohlcv_df(3).iterrows():
            rec.append(row, _make_signal_result(), position=0)

        csv_path = rec.to_csv(tmp_path / "signals.csv")
        pq_path = rec.to_parquet(tmp_path / "signals.parquet")

        assert csv_path.exists()
        assert pq_path.exists()

        read_back = pd.read_csv(csv_path)
        assert len(read_back) == 3

    def test_signal_recorder_empty_dataframe(self):
        rec = SignalRecorder("X.Y", "X.Y-1-MINUTE-LAST-EXTERNAL")
        df = rec.to_dataframe()
        assert df.empty
        assert "ts_event" in df.columns


class TestDryRunExecutionRecorder:

    def test_dry_run_records_entry_intent(self):
        rec = DryRunExecutionRecorder("BTCUSDT-PERP.BINANCE", trade_size=0.01)
        row = _make_ohlcv_df(1).iloc[0]
        result = _make_signal_result(entry=True)

        rec.append(row, result)

        assert len(rec) == 1
        df = rec.to_dataframe()
        assert df["side"].iloc[0] == "SELL"
        assert df["status"].iloc[0] == "dry_run_intent"
        assert df["quantity"].iloc[0] == pytest.approx(0.01)

    def test_dry_run_records_exit_intent(self):
        rec = DryRunExecutionRecorder("BTCUSDT-PERP.BINANCE")
        row = _make_ohlcv_df(1).iloc[0]
        result = _make_signal_result(exit_=True)

        rec.append(row, result)

        df = rec.to_dataframe()
        assert df["side"].iloc[0] == "BUY"
        assert df["order_type"].iloc[0] == "market"

    def test_dry_run_no_signal_records_nothing(self):
        rec = DryRunExecutionRecorder("BTCUSDT-PERP.BINANCE")
        row = _make_ohlcv_df(1).iloc[0]
        result = _make_signal_result()   # no entry, no exit

        rec.append(row, result)

        assert len(rec) == 0

    def test_dry_run_writes_csv(self, tmp_path):
        rec = DryRunExecutionRecorder("BTCUSDT-PERP.BINANCE")
        row = _make_ohlcv_df(1).iloc[0]
        rec.append(row, _make_signal_result(entry=True))

        path = rec.to_csv(tmp_path / "orders.csv")
        assert path.exists()
        df = pd.read_csv(path)
        assert "status" in df.columns

    def test_dry_run_status_always_dry_run_intent(self):
        """status column must always be 'dry_run_intent' — never a real order status."""
        rec = DryRunExecutionRecorder("X.Y")
        row = _make_ohlcv_df(1).iloc[0]
        rec.append(row, _make_signal_result(entry=True))
        rec.append(row, _make_signal_result(exit_=True))

        df = rec.to_dataframe()
        assert all(df["status"] == "dry_run_intent")


class TestCcxtPaperLiveRunner:

    def test_paper_live_runner_stops_at_max_bars(self):
        """Runner terminates after receiving exactly max_bars new bars."""
        config = _base_config(poll_interval_seconds=0.001)
        engine = _MockSignalEngine()
        feed = _PollCountFeed(rows_per_poll=2)   # 2 bars per poll

        runner = CcxtPaperLiveRunner(config, engine, _feed=feed)
        summary = runner.run(max_bars=3)

        assert summary["total_bars"] == 3
        # Signal engine must have been called exactly 3 times (warmup=0 + live=3)
        assert len(engine.calls) == 3

    def test_paper_live_runner_no_output_without_output_dir(self, tmp_path):
        """Run without output_dir: no files written, summary returned cleanly."""
        config = _base_config(poll_interval_seconds=0.001)
        feed = _PollCountFeed(rows_per_poll=1)
        runner = CcxtPaperLiveRunner(config, _MockSignalEngine(), _feed=feed)

        summary = runner.run(max_bars=2)

        assert summary["total_bars"] == 2
        assert summary["total_signals"] == 2   # one per bar

    def test_paper_live_runner_saves_outputs(self, tmp_path):
        """With output_dir set, CSV/JSON artefacts are written."""
        config = _base_config(
            poll_interval_seconds=0.001,
            output_dir=str(tmp_path / "live_out"),
        )
        feed = _PollCountFeed(rows_per_poll=2)
        runner = CcxtPaperLiveRunner(config, _MockSignalEngine(), _feed=feed)
        runner.run(max_bars=2)

        out = tmp_path / "live_out"
        assert (out / "received_bars.csv").exists()
        assert (out / "signals.csv").exists()
        assert (out / "orders.csv").exists()
        assert (out / "run_info.json").exists()

    def test_paper_live_runner_position_updates_on_entry(self):
        """Position flips to -1 when signal engine emits an entry."""
        class _EntryEngine:
            def update(self, bar, position=0, bars_since_entry=0):
                return _make_signal_result(entry=True)

        config = _base_config(poll_interval_seconds=0.001)
        feed = _PollCountFeed(rows_per_poll=1)
        runner = CcxtPaperLiveRunner(config, _EntryEngine(), _feed=feed)
        runner.run(max_bars=1)

        assert runner._position == -1

    def test_paper_live_runner_position_resets_on_exit(self):
        """Position returns to 0 when exit signal is received."""
        call_count = {"n": 0}

        class _EntryThenExitEngine:
            def update(self, bar, position=0, bars_since_entry=0):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return _make_signal_result(entry=True)
                return _make_signal_result(exit_=True)

        config = _base_config(poll_interval_seconds=0.001)
        feed = _PollCountFeed(rows_per_poll=1)
        runner = CcxtPaperLiveRunner(config, _EntryThenExitEngine(), _feed=feed)
        runner.run(max_bars=2)

        assert runner._position == 0


class TestCcxtPollingConfig:

    def test_enable_order_submit_raises(self):
        """Constructing config with enable_order_submit=True must raise NotImplementedError."""
        with pytest.raises(NotImplementedError, match="enable_order_submit"):
            CcxtPollingLiveConfig(
                exchange_id="binance",
                market_type="swap",
                symbol="BTC/USDT:USDT",
                timeframe="1m",
                venue="BINANCE",
                enable_order_submit=True,
            )

    def test_bad_timeframe_raises(self):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            CcxtPollingLiveConfig(
                exchange_id="binance", market_type="swap",
                symbol="BTC/USDT:USDT", timeframe="7m", venue="BINANCE",
            )

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError, match="symbol is required"):
            CcxtPollingLiveConfig(
                exchange_id="binance", market_type="swap",
                symbol="", timeframe="1m", venue="BINANCE",
            )

    def test_nautilus_timeframe_property(self):
        config = _base_config(timeframe="1h")
        assert config.nautilus_timeframe == "1-HOUR"

    def test_resolved_venue_fallback(self):
        config = _base_config(venue="")
        assert config.resolved_venue == "BINANCE"

    def test_tf_ms_property(self):
        assert _base_config(timeframe="1m").tf_ms == 60_000
        assert _base_config(timeframe="1h").tf_ms == 3_600_000
        assert _base_config(timeframe="1d").tf_ms == 86_400_000


class TestExistingConnectorNotBroken:

    def test_existing_ccxt_historical_tests_not_broken(self):
        """Importing the historical ccxt connector must still succeed."""
        from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
        cfg = CcxtDataConfig(
            exchange_id="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframe="1h",
            since="2024-01-01T00:00:00Z",
        )
        assert cfg.nautilus_timeframe == "1-HOUR"
        assert cfg.resolved_venue == "BINANCE"

    def test_ccxt_live_package_lazy_import(self):
        """nautilus_ext.ccxt_live __all__ must be present without Nautilus."""
        from nautilus_ext.ccxt_live import __all__ as live_all
        assert "CcxtPollingLiveConfig" in live_all
        assert "CcxtPaperLiveRunner" in live_all
