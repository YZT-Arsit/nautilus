"""分钟线 / bar 变换测试（纯标准库，无需 polars/pyarrow）。"""
from __future__ import annotations

import pytest

from data_engine.events import BarEvent
from data_engine.transforms import (
    aggregate_ticks_to_bars,
    derive_trading_date,
    parse_frequency,
    resample_bars,
    validate_bars,
)

ONE_S = 1_000_000_000


def _ticks(n, *, instrument="IH2303.CFFEX", size=2.0, start=0):
    return [
        {"instrument_id": instrument, "event_time_ns": (start + i) * ONE_S,
         "price": 100.0 + (i % 10) * 0.1, "size": size}
        for i in range(n)
    ]


class TestFrequency:
    @pytest.mark.parametrize("freq,ns", [
        ("1s", ONE_S), ("1m", 60 * ONE_S), ("5m", 300 * ONE_S),
        ("15m", 900 * ONE_S), ("1h", 3600 * ONE_S),
    ])
    def test_parse(self, freq, ns):
        assert parse_frequency(freq) == ns

    @pytest.mark.parametrize("bad", ["", "m", "0m", "-1m", "1w", "abc"])
    def test_bad_raises(self, bad):
        with pytest.raises(ValueError):
            parse_frequency(bad)


class TestAggregate:
    def test_one_minute_bucketing(self):
        res = aggregate_ticks_to_bars(_ticks(120), frequency="1m")
        assert len(res.bars) == 2
        assert [b.event_time_ns for b in res.bars] == [0, 60 * ONE_S]
        assert not res.issues

    def test_ohlc_and_volume(self):
        res = aggregate_ticks_to_bars(_ticks(60), frequency="1m")
        b = res.bars[0]
        assert b.low <= b.open <= b.high and b.low <= b.close <= b.high
        assert b.volume == 60 * 2.0  # 60 ticks × size 2
        assert not res.volume_is_synthetic

    def test_synthetic_volume_flagged(self):
        res = aggregate_ticks_to_bars(
            [{"instrument_id": "X", "event_time_ns": 0, "price": 10.0},
             {"instrument_id": "X", "event_time_ns": ONE_S, "price": 11.0}],
            frequency="1m",
        )
        assert res.volume_is_synthetic
        assert res.bars[0].volume == 2.0  # tick count
        assert res.rows[0]["volume_is_synthetic"] is True

    def test_rows_schema(self):
        res = aggregate_ticks_to_bars(_ticks(5), frequency="1m", trading_date="2026-05-26")
        row = res.rows[0]
        for col in ("instrument_id", "symbol", "ts_event", "open", "high", "low",
                    "close", "volume", "turnover", "trading_date", "frequency",
                    "volume_is_synthetic"):
            assert col in row
        assert row["trading_date"] == "2026-05-26"
        assert row["frequency"] == "1m"

    def test_unsorted_input_is_sorted(self):
        ticks = list(reversed(_ticks(120)))
        res = aggregate_ticks_to_bars(ticks, frequency="1m")
        ts = [b.event_time_ns for b in res.bars]
        assert ts == sorted(ts)

    def test_multi_instrument(self):
        ticks = _ticks(60, instrument="A") + _ticks(60, instrument="B")
        res = aggregate_ticks_to_bars(ticks, frequency="1m")
        insts = {b.instrument_id for b in res.bars}
        assert insts == {"A", "B"}

    def test_missing_instrument_raises(self):
        with pytest.raises(ValueError):
            aggregate_ticks_to_bars([{"event_time_ns": 0, "price": 1.0}], frequency="1m")

    def test_price_fallback_bid_ask(self):
        res = aggregate_ticks_to_bars(
            [{"instrument_id": "X", "event_time_ns": 0, "bid": 10.0, "ask": 12.0}],
            frequency="1m",
        )
        assert res.bars[0].close == 11.0


class TestResample:
    def test_one_to_five_minute(self):
        res = aggregate_ticks_to_bars(_ticks(600), frequency="1m")  # 10 one-min bars
        assert len(res.bars) == 10
        five = resample_bars(res.bars, "5m")
        assert len(five) == 2
        assert five[0].volume == sum(b.volume for b in res.bars[:5])


class TestValidate:
    def test_clean_bars_no_issues(self):
        res = aggregate_ticks_to_bars(_ticks(120), frequency="1m")
        assert validate_bars(res.bars) == []

    def test_detects_bad_ohlc(self):
        bad = BarEvent(close=10, open=10, high=5, low=8, volume=1,
                       instrument_id="X", event_time_ns=0)
        assert validate_bars([bad])

    def test_detects_duplicate_and_nonmonotonic(self):
        a = BarEvent(close=10, open=10, high=10, low=10, volume=1, instrument_id="X", event_time_ns=2 * ONE_S)
        dup = BarEvent(close=10, open=10, high=10, low=10, volume=1, instrument_id="X", event_time_ns=2 * ONE_S)
        back = BarEvent(close=10, open=10, high=10, low=10, volume=1, instrument_id="X", event_time_ns=ONE_S)
        issues = validate_bars([a, dup, back])
        assert any("重复" in i for i in issues)
        assert any("单调" in i for i in issues)


def test_derive_trading_date():
    assert derive_trading_date(0) == "1970-01-01"
