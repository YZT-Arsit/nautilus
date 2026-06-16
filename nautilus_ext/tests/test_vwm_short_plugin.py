"""Unit tests for the vwm_short StrategyPlugin and the Binance-parquet read path.

Layers:

* **Plugin wiring** (no Nautilus needed) - registry entry, build_specs shape,
  and the datetime->ns timestamp helper used to read Binance Vision's ``ts``.
* **Parquet read** (needs pyarrow) - a Hive dataset with a datetime ``ts``
  column is read into BarEvents via ``data_engine`` exactly as the real
  Binance Vision layout would be.

The end-to-end native run (real Nautilus BacktestEngine over real Binance data)
is covered by the server validation, not here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


def test_vwm_short_registered():
    from strategy_framework.registry import STRATEGY_REGISTRY, get_entry

    assert "vwm_short" in STRATEGY_REGISTRY
    plugin = get_entry("vwm_short")
    assert plugin.name == "vwm_short"
    assert plugin.default_config_path == "strategies/vwm_short/config.yaml"


def test_build_specs_are_window1_passthrough():
    from strategies.vwm_short import VwmShortConfig, build_specs

    specs = build_specs(VwmShortConfig())
    fields = {s.input_field for s in specs}
    assert fields == {"close", "high", "low", "volume"}
    assert all(s.window == 1 for s in specs)
    assert all(s.params.get("type") == "rolling_mean" for s in specs)


def test_to_event_time_ns_accepts_datetime():
    from data_engine.time import to_event_time_ns

    # 2024-06-01T00:05:00Z -> known epoch ns. Naive datetime is treated as UTC.
    dt = datetime(2024, 6, 1, 0, 5, 0)
    expected = int(datetime(2024, 6, 1, 0, 5, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    assert to_event_time_ns(dt, "ns") == expected
    # An explicit UTC datetime gives the same instant.
    assert to_event_time_ns(dt.replace(tzinfo=timezone.utc), "us") == expected
    # Numeric values still work (regression guard).
    assert to_event_time_ns("1000", "ms") == 1_000_000_000


def _write_binance_like_dataset(root, n=30):
    """Write a tiny Hive dataset mirroring Binance Vision's layout/columns."""
    import pyarrow as pa
    import pyarrow.dataset as ds

    base_ns = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    step_ns = 5 * 60 * 1_000_000_000  # 5 minutes
    ts = [
        datetime.fromtimestamp((base_ns + i * step_ns) / 1e9, tz=timezone.utc).replace(tzinfo=None)
        for i in range(n)
    ]
    close = [100.0 - i for i in range(n)]  # steady decline
    table = pa.table(
        {
            "ts": pa.array(ts, type=pa.timestamp("us")),
            "open": close,
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [10.0 + i for i in range(n)],
            "exchange": ["BINANCE"] * n,
            "venue_type": ["spot"] * n,
            "symbol": ["BTCUSDT"] * n,
            "bar_type": ["5m"] * n,
            "date": ["2024-06-01"] * n,
        }
    )
    ds.write_dataset(
        table,
        base_dir=str(root),
        format="parquet",
        partitioning=["exchange", "venue_type", "symbol", "bar_type", "date"],
        partitioning_flavor="hive",
    )
    return n


def test_binance_like_parquet_reads_into_bar_events(tmp_path):
    pytest.importorskip("pyarrow")
    from data_engine.loader import load_events

    n = _write_binance_like_dataset(tmp_path / "market_data")
    data_cfg = {
        "mode": "hive_parquet_bars",
        "root": str(tmp_path / "market_data"),
        "instrument_id": "BTCUSDT.BINANCE",
        "warmup_bars": 0,
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {"exchange": "BINANCE", "venue_type": "spot",
                    "symbol": "BTCUSDT", "bar_type": "5m"},
    }
    warmup, live = load_events(data_cfg)
    live = list(live)
    assert len(warmup) == 0
    assert len(live) == n
    # Timestamps strictly ascending and labelled with our instrument_id.
    times = [b.event_time_ns for b in live]
    assert times == sorted(times)
    assert times[0] == int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    assert all(b.instrument_id == "BTCUSDT.BINANCE" for b in live)
    # OHLCV survived the round-trip.
    assert live[0].close == 100.0
    assert live[0].volume == 10.0
