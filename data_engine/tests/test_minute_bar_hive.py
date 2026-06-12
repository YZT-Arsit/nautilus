"""Minute bar Hive Parquet write/read validation."""
from __future__ import annotations

import pytest

pytest.importorskip("polars")
pytest.importorskip("pyarrow")

from feature_engine.services import MinuteBarBuilder
from feature_engine.storage.market_reader import MarketDataReader

ONE_S = 1_000_000_000


def test_minute_bars_write_hive_parquet_and_read_back(tmp_path) -> None:
    ticks = [
        {
            "instrument_id": "IH2303.CFFEX",
            "event_time_ns": i * ONE_S,
            "price": 100.0 + (i % 10) * 0.25,
            "size": 2.0,
        }
        for i in range(120)
    ]
    root = tmp_path / "market_data"
    builder = MinuteBarBuilder(asset_class="future", exchange="CFFEX")

    result, paths = builder.build_and_write(
        ticks,
        instrument_id="IH2303.CFFEX",
        market_root=root,
        frequency="1m",
        trading_date="2026-05-26",
    )

    assert paths
    assert not result.issues
    assert result.volume_is_synthetic is False

    df = MarketDataReader(root).scan(
        asset_class="future",
        exchange="CFFEX",
        frequency="1m",
        trading_date="2026-05-26",
        instrument_id="IH2303.CFFEX",
    ).sort("ts_event")

    assert df.height == len(result.bars) == 2
    assert df["volume_is_synthetic"].to_list() == [False, False]
    assert df["ts_event"].is_duplicated().sum() == 0
    assert df["ts_event"].to_list() == sorted(df["ts_event"].to_list())
    invalid_ohlc = df.filter(
        (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    )
    assert invalid_ohlc.is_empty()


def test_minute_bars_synthetic_volume_flag_round_trips(tmp_path) -> None:
    ticks = [
        {
            "instrument_id": "IF2303.CFFEX",
            "event_time_ns": i * ONE_S,
            "price": 3000.0 + i,
        }
        for i in range(60)
    ]
    root = tmp_path / "market_data"
    builder = MinuteBarBuilder(asset_class="future", exchange="CFFEX")

    result, _ = builder.build_and_write(
        ticks,
        instrument_id="IF2303.CFFEX",
        market_root=root,
        frequency="1m",
        trading_date="2026-05-26",
    )

    df = MarketDataReader(root).scan(
        frequency="1m",
        trading_date="2026-05-26",
        instrument_id="IF2303.CFFEX",
    )
    assert result.volume_is_synthetic is True
    assert df["volume_is_synthetic"].to_list() == [True]
