"""Tests for the TradeEvent data path (data_engine).

Covers:
  * TradeEvent dataclass + trade_adapter (quote_quantity fill, side derivation),
  * synthetic_trades source via load_events,
  * parquet_trades / hive_parquet_trades read of a StandardTrade Hive dataset
    (datetime ts -> ns, partition filters, first/last TradeEvent content).

The parquet test needs pyarrow (server-only); it is importorskip-gated.
No nautilus_trader is imported anywhere in this path.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data_engine.adapters.trade_adapter import make_trade_event, side_from_is_buyer_maker
from data_engine.events import TradeEvent
from data_engine.loader import load_events


def test_trade_event_dataclass_and_adapter():
    e = make_trade_event(price=100.0, quantity=2.0, instrument_id="BTCUSDT.BINANCE",
                         event_time_ns=123, is_buyer_maker=True, trade_id=7)
    assert isinstance(e, TradeEvent)
    assert e.event_type == "trade"
    assert e.quote_quantity == pytest.approx(200.0)   # filled price*quantity
    assert e.side == "SELL"                            # is_buyer_maker=True -> SELL
    assert e.trade_id == 7


def test_side_derivation():
    assert side_from_is_buyer_maker(True) == "SELL"
    assert side_from_is_buyer_maker(False) == "BUY"
    assert side_from_is_buyer_maker(None) is None


def test_synthetic_trades_via_load_events():
    warmup, live = load_events({
        "mode": "synthetic_trades",
        "instrument_id": "BTCUSDT.BINANCE",
        "n_trades": 10,
        "warmup": 2,
    })
    live = list(live)
    assert len(warmup) == 2
    assert len(live) == 8
    assert all(isinstance(t, TradeEvent) for t in live)
    assert all(t.event_type == "trade" for t in live)
    assert live[0].instrument_id == "BTCUSDT.BINANCE"
    # alternating BUY/SELL, monotonic timestamps
    times = [t.event_time_ns for t in live]
    assert times == sorted(times)
    assert {t.side for t in live} == {"BUY", "SELL"}


def _write_standard_trades(root, n=20):
    import pyarrow as pa
    import pyarrow.dataset as ds

    base_ns = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    step_ns = 1_000_000_000
    ts = [
        datetime.fromtimestamp((base_ns + i * step_ns) / 1e9, tz=timezone.utc).replace(tzinfo=None)
        for i in range(n)
    ]
    prices = [100.0 + i for i in range(n)]
    qty = [1.0 + (i % 3) for i in range(n)]
    is_maker = [(i % 2 == 1) for i in range(n)]  # alternate
    table = pa.table({
        "ts": pa.array(ts, type=pa.timestamp("us")),
        "agg_trade_id": list(range(n)),
        "price": prices,
        "quantity": qty,
        "quote_quantity": [p * q for p, q in zip(prices, qty)],
        "first_trade_id": list(range(n)),
        "last_trade_id": list(range(n)),
        "is_buyer_maker": is_maker,
        "side": ["SELL" if m else "BUY" for m in is_maker],
        "source": ["binance_vision_aggTrades"] * n,
        "exchange": ["BINANCE"] * n,
        "venue_type": ["spot"] * n,
        "symbol": ["BTCUSDT"] * n,
        "data_type": ["aggTrades"] * n,
        "date": ["2024-06-01"] * n,
    })
    ds.write_dataset(
        table, base_dir=str(root), format="parquet",
        partitioning=["exchange", "venue_type", "symbol", "data_type", "date"],
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
    )
    return n


def _write_bars_5m(root, n=10):
    """Write a `bar_type=5m` Hive partition (open/high/low/close/volume, NO price)."""
    import pyarrow as pa
    import pyarrow.dataset as ds

    base_ns = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    step_ns = 300 * 1_000_000_000  # 5 minutes
    ts = [
        datetime.fromtimestamp((base_ns + i * step_ns) / 1e9, tz=timezone.utc).replace(tzinfo=None)
        for i in range(n)
    ]
    table = pa.table({
        "ts": pa.array(ts, type=pa.timestamp("us")),
        "open": [1.0 + i for i in range(n)],
        "high": [2.0 + i for i in range(n)],
        "low": [0.5 + i for i in range(n)],
        "close": [1.5 + i for i in range(n)],
        "volume": [10.0 + i for i in range(n)],
        "exchange": ["BINANCE"] * n,
        "venue_type": ["spot"] * n,
        "symbol": ["BTCUSDT"] * n,
        "bar_type": ["5m"] * n,
        "date": ["2024-06-01"] * n,
    })
    ds.write_dataset(
        table, base_dir=str(root), format="parquet",
        partitioning=["exchange", "venue_type", "symbol", "bar_type", "date"],
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
    )
    return n


def test_mixed_root_trade_loader_ignores_bars(tmp_path):
    """Regression: a unified root holding BOTH bar_type=5m and data_type=aggTrades
    partitions must load only the trade rows (no 'price missing' from bar schema)."""
    pytest.importorskip("pyarrow")
    root = tmp_path / "market_data"
    _write_bars_5m(root, n=10)           # bars have close/volume, no price
    n = _write_standard_trades(root)     # trades have price/quantity/side

    warmup, live = load_events({
        "mode": "hive_parquet_trades",
        "root": str(root),
        "instrument_id": "BTCUSDT.BINANCE",
        "warmup": 0,
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {"exchange": "BINANCE", "venue_type": "spot",
                    "symbol": "BTCUSDT", "data_type": "aggTrades"},
    })
    live = list(live)
    # Only the trade rows are read — the 10 bar rows are ignored.
    assert len(live) == n
    assert all(isinstance(t, TradeEvent) for t in live)
    times = [t.event_time_ns for t in live]
    assert times == sorted(times)
    first = live[0]
    assert first.price == pytest.approx(100.0)   # trade price, NOT bar close (1.5)
    assert first.quantity == pytest.approx(1.0)
    assert first.side == "BUY"                    # is_buyer_maker False on i=0


def test_hive_parquet_trades_read(tmp_path):
    pytest.importorskip("pyarrow")
    n = _write_standard_trades(tmp_path / "market_data")
    warmup, live = load_events({
        "mode": "hive_parquet_trades",
        "root": str(tmp_path / "market_data"),
        "instrument_id": "BTCUSDT.BINANCE",
        "warmup": 0,
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {"exchange": "BINANCE", "venue_type": "spot",
                    "symbol": "BTCUSDT", "data_type": "aggTrades"},
    })
    live = list(live)
    assert len(warmup) == 0
    assert len(live) == n
    assert all(isinstance(t, TradeEvent) for t in live)
    times = [t.event_time_ns for t in live]
    assert times == sorted(times)
    assert times[0] == int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    first, last = live[0], live[-1]
    assert first.instrument_id == "BTCUSDT.BINANCE"
    assert first.price == pytest.approx(100.0)
    assert first.side == "BUY"          # is_buyer_maker False on i=0
    assert last.price == pytest.approx(100.0 + (n - 1))
    assert first.quote_quantity == pytest.approx(first.price * first.quantity)
