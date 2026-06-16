"""Native end-to-end smoke: Binance-style parquet -> vwm_short -> Nautilus engine.

Builds a tiny Hive Parquet dataset in the Binance Vision layout (datetime ``ts``
column, ``exchange/venue_type/symbol/bar_type/date`` partitions), then runs the
FULL ``run_strategy`` chain with ``mode=nautilus_native``:

    hive_parquet_bars -> feature_engine -> vwm_short (Mode B) -> SignalToOrderPolicy
        -> NautilusBacktestBackend -> Nautilus BacktestEngine -> report

Guarded by ``importorskip`` (nautilus_trader / pandas / pyarrow), so it skips on
the dev laptop and runs on the backtest server. No network: data is fabricated
locally but flows through the exact real-data code path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_CONFIG = REPO_ROOT / "configs" / "backtests" / "vwm_short_binance_vision_nautilus.yaml"


def _write_dataset(root: Path) -> int:
    """Decline then recover, with positive volume - provokes a short entry/exit."""
    import pyarrow as pa
    import pyarrow.dataset as ds

    decline = [100.0 - 1.5 * i for i in range(28)]      # downtrend
    recover = [decline[-1] + 1.5 * i for i in range(1, 18)]  # uptrend
    close = decline + recover
    n = len(close)
    base_ns = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    step_ns = 5 * 60 * 1_000_000_000
    ts = [
        datetime.fromtimestamp((base_ns + i * step_ns) / 1e9, tz=timezone.utc).replace(tzinfo=None)
        for i in range(n)
    ]
    table = pa.table(
        {
            "ts": pa.array(ts, type=pa.timestamp("us")),
            "open": close,
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [25.0] * n,
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


def test_base_config_is_well_formed():
    cfg = yaml.safe_load(BASE_CONFIG.read_text())
    assert cfg["strategy"] == "vwm_short"
    assert cfg["data"]["mode"] == "hive_parquet_bars"
    assert cfg["data"]["timestamp_column"] == "ts"
    assert cfg["execution"]["mode"] == "nautilus_native"
    assert cfg["execution"]["sell_means"] == "short"
    assert cfg["execution"]["allow_short"] is True


def test_native_vwm_short_over_binance_like_parquet(tmp_path: Path):
    pytest.importorskip("nautilus_trader")
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    import run_strategy

    market_data = tmp_path / "market_data"
    n = _write_dataset(market_data)

    cfg = yaml.safe_load(BASE_CONFIG.read_text())
    cfg["run_name"] = "vwm_short_smoke"
    cfg["data"]["root"] = str(market_data)
    cfg["output"] = {"root": str(tmp_path), "print_table": False}
    config_path = tmp_path / "vwm_short_smoke.yaml"
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    run_strategy.main(["--config", str(config_path)])

    out_dir = tmp_path / "vwm_short_smoke"
    metrics = json.loads((out_dir / "metrics.json").read_text())
    assert (out_dir / "report.md").exists()
    assert metrics["mode"] == "nautilus_native"
    assert metrics["bar_count"] == n
    assert metrics["engine"].get("engine") == "BacktestEngine"
    assert metrics["engine"].get("account_type") == "MARGIN"  # allow_short -> margin
    for key in ("total_return", "max_drawdown", "trade_count", "win_rate",
                "final_equity", "initial_cash", "fill_count", "signal_count"):
        assert key in metrics, key
    # Every emitted short entry must produce a real Nautilus fill.
    if metrics.get("signal_breakdown", {}).get("SELL", 0) >= 1:
        assert metrics["fill_count"] >= 1
