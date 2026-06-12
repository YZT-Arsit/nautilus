"""Tests for the BarEvent <-> Polars DataFrame bridge (data_engine).

All tests are local — no network, no pandas.
"""
from __future__ import annotations

import pytest

pl = pytest.importorskip("polars")

from data_engine import BarEvent, bars_to_polars, polars_to_bars
from data_engine.adapters.dataframe_adapter import (
    _EVENT_TIME_NS,
    _SYMBOL,
    _TS_EVENT,
)
from data_engine.time import ONE_SECOND_NS


def _sample_bars() -> list[BarEvent]:
    return [
        BarEvent(
            close=100.0 + i,
            open=99.0 + i,
            high=101.0 + i,
            low=98.0 + i,
            volume=1000.0 + i,
            instrument_id="IH2303.CFFEX",
            event_time_ns=i * ONE_SECOND_NS,
        )
        for i in range(5)
    ]


def test_bars_to_polars_columns_and_mapping() -> None:
    df = bars_to_polars(_sample_bars())
    # feature_engine 期待的列名存在。
    assert _SYMBOL in df.columns
    assert _TS_EVENT in df.columns
    # 原始字段也保留，便于无损回转。
    assert _EVENT_TIME_NS in df.columns
    assert "instrument_id" in df.columns
    # symbol == instrument_id
    assert df["symbol"].to_list() == df["instrument_id"].to_list()
    assert df.height == 5
    assert df["close"].to_list() == [100.0, 101.0, 102.0, 103.0, 104.0]


def test_bars_to_polars_ts_event_is_utc_datetime() -> None:
    df = bars_to_polars(_sample_bars())
    dtype = df.schema[_TS_EVENT]
    assert isinstance(dtype, pl.Datetime)
    assert dtype.time_zone == "UTC"


def test_bars_to_polars_empty_returns_typed_empty_frame() -> None:
    df = bars_to_polars([])
    assert df.height == 0
    # schema 仍然完整，下游 concat/sort 不会因缺列报错。
    assert _SYMBOL in df.columns
    assert _TS_EVENT in df.columns


def test_polars_to_bars_round_trip_preserves_ohlcv_id_timestamp() -> None:
    original = _sample_bars()
    df = bars_to_polars(original)
    restored = polars_to_bars(df)
    assert len(restored) == len(original)
    for a, b in zip(original, restored):
        assert a.instrument_id == b.instrument_id
        assert a.event_time_ns == b.event_time_ns
        assert a.open == b.open
        assert a.high == b.high
        assert a.low == b.low
        assert a.close == b.close
        assert a.volume == b.volume


def test_polars_to_bars_derives_ns_from_ts_event_when_no_event_time_ns() -> None:
    original = _sample_bars()
    df = bars_to_polars(original).drop(_EVENT_TIME_NS)
    restored = polars_to_bars(df)
    assert [b.event_time_ns for b in restored] == [
        b.event_time_ns for b in original
    ]


def test_polars_to_bars_uses_symbol_when_no_instrument_id() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["BTC/USDT"],
            "event_time_ns": [123 * ONE_SECOND_NS],
            "close": [42000.0],
        }
    )
    bars = polars_to_bars(df)
    assert len(bars) == 1
    assert bars[0].instrument_id == "BTC/USDT"
    # 缺失的 OHLC 用 close 兜底，volume 用 0.0。
    assert bars[0].open == 42000.0
    assert bars[0].volume == 0.0


def test_polars_to_bars_empty_returns_empty_list() -> None:
    assert polars_to_bars(bars_to_polars([])) == []


def test_polars_to_bars_missing_timestamp_raises() -> None:
    df = pl.DataFrame({"symbol": ["X"], "close": [1.0]})
    with pytest.raises(ValueError, match="时间戳"):
        polars_to_bars(df)


def test_polars_to_bars_missing_symbol_raises() -> None:
    df = pl.DataFrame({"event_time_ns": [1], "close": [1.0]})
    with pytest.raises(ValueError, match="标的列"):
        polars_to_bars(df)
