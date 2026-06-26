from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from scripts.ingest_crypto_perpetual_bars import _canonicalize_frame
from scripts.ingest_crypto_perpetual_bars import build_plan
from scripts.ingest_crypto_perpetual_bars import execute_plan
from scripts.ingest_crypto_perpetual_bars import main


def test_binance_usdm_plan_builds_public_archive_url(tmp_path):
    plan = build_plan(
        symbols=["BTCUSDT"],
        bar_type="5m",
        start="2024-06-01",
        end="2024-06-01",
        out_root=tmp_path,
    )
    item = plan[0]
    assert item.url == (
        "https://data.binance.vision/data/futures/um/daily/klines/"
        "BTCUSDT/5m/BTCUSDT-5m-2024-06-01.zip"
    )
    assert "venue_type=futures_um" in item.output_path
    assert "venue_type=spot" not in item.output_path
    assert item.instrument_id == "BTCUSDT-PERP.BINANCE"


def test_sol_bnb_usdm_plan_builds_public_archive_urls(tmp_path):
    plan = build_plan(
        symbols=["SOLUSDT", "BNBUSDT"],
        bar_type="5m",
        start="2024-06-01",
        end="2024-06-01",
        out_root=tmp_path,
    )
    urls = {item.symbol: item.url for item in plan}
    assert urls["SOLUSDT"] == (
        "https://data.binance.vision/data/futures/um/daily/klines/"
        "SOLUSDT/5m/SOLUSDT-5m-2024-06-01.zip"
    )
    assert urls["BNBUSDT"] == (
        "https://data.binance.vision/data/futures/um/daily/klines/"
        "BNBUSDT/5m/BNBUSDT-5m-2024-06-01.zip"
    )
    assert all("venue_type=futures_um" in item.output_path for item in plan)
    assert all("venue_type=spot" not in item.output_path for item in plan)
    assert {item.instrument_id for item in plan} == {"SOLUSDT-PERP.BINANCE", "BNBUSDT-PERP.BINANCE"}


def test_plan_only_no_writes(tmp_path):
    rc = main(
        [
            "--symbols",
            "BTCUSDT,ETHUSDT",
            "--bar-type",
            "5m",
            "--start",
            "2024-06-01",
            "--end",
            "2024-06-01",
            "--out-root",
            str(tmp_path),
            "--plan-only",
            "--max-symbols",
            "4",
        ]
    )
    assert rc == 0
    assert not any(tmp_path.rglob("*.parquet"))


def test_max_symbols_and_days_guards(tmp_path):
    with pytest.raises(ValueError, match="max-symbols"):
        build_plan(
            symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
            bar_type="5m",
            start="2024-06-01",
            end="2024-06-01",
            out_root=tmp_path,
        )
    with pytest.raises(ValueError, match="max-days"):
        build_plan(
            symbols=["BTCUSDT"],
            bar_type="5m",
            start="2024-06-01",
            end="2024-06-02",
            out_root=tmp_path,
        )


def test_max_days_override_enumerates_window(tmp_path):
    # opt-in multi-day range: default guard stays 1, but a raised max_days
    # enumerates the full inclusive window (one plan item per day).
    plan = build_plan(
        symbols=["BTCUSDT"],
        bar_type="15m",
        start="2024-06-01",
        end="2024-06-03",
        out_root=tmp_path,
        max_days=92,
    )
    dates = [item.date for item in plan]
    assert dates == ["2024-06-01", "2024-06-02", "2024-06-03"]
    assert plan[-1].url.endswith("BTCUSDT-15m-2024-06-03.zip")


def test_schema_normalization_adds_trade_bar_fields():
    import polars as pl

    df = pl.DataFrame(
        {
            "ts": [datetime(2024, 6, 1, 0, 0)],
            "exchange": ["BINANCE"],
            "venue_type": ["futures_um"],
            "symbol": ["BTCUSDT"],
            "instrument_id": ["BTCUSDT"],
            "bar_type": ["5m"],
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "volume": [10.0],
            "quote_volume": [15.0],
            "trade_count": [3],
            "taker_buy_volume": [5.0],
            "taker_buy_quote_volume": [7.5],
            "source": ["binance_vision"],
            "ingested_at": [datetime(2026, 1, 1, 0, 0)],
        }
    )
    out = _canonicalize_frame(df, symbol="BTCUSDT", bar_type="5m")
    row = out.to_dicts()[0]
    assert row["instrument_id"] == "BTCUSDT-PERP.BINANCE"
    assert row["source"] == "binance_vision_futures_um_klines"
    assert row["bar_source"] == "trade_bar"
    assert row["is_trade_bar"] is True


def test_existing_output_skip_no_overwrite(tmp_path):
    plan = build_plan(
        symbols=["BTCUSDT"],
        bar_type="5m",
        start="2024-06-01",
        end="2024-06-01",
        out_root=tmp_path,
    )
    out = Path(plan[0].output_path)
    out.parent.mkdir(parents=True)
    out.write_bytes(b"existing")
    results = execute_plan(plan, out_root=tmp_path, no_overwrite=True, timeout=1)
    assert results[0].status == "skipped_existing"
    assert out.read_bytes() == b"existing"


def test_no_private_endpoint_or_api_key_terms():
    text = Path("scripts/ingest_crypto_perpetual_bars.py").read_text(encoding="utf-8")
    forbidden = (
        "api_key",
        "secret",
        "priv" + "ate",
        "acc" + "ount",
        "bal" + "ance",
        "pos" + "ition",
        "lev" + "erage",
        "can" + "cel",
        "create_" + "order",
        "shell" + "=True",
    )
    for token in forbidden:
        assert token not in text
