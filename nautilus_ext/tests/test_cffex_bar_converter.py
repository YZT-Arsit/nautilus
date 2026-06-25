from __future__ import annotations

from datetime import datetime, timezone

import pytest

from research.cffex_bar_converter import BAR_COLUMNS
from research.cffex_bar_converter import depth_rows_to_mid_bars
from research.cffex_bar_converter import partition_path
from research.cffex_bar_converter import quote_rows_to_mid_bars
from research.cffex_bar_converter import write_mid_bars


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_quote_ticks_to_1m_ohlc_mid_bars_tick_count_volume():
    rows = [
        {"ts_event": _ts("2023-01-03T01:29:00.200000+00:00"), "bid_price": 99.0, "ask_price": 101.0},
        {"ts_event": _ts("2023-01-03T01:29:30+00:00"), "bid_price": 101.0, "ask_price": 103.0},
        {"ts_event": _ts("2023-01-03T01:29:59+00:00"), "bid_price": 100.0, "ask_price": 102.0},
        {"ts_event": _ts("2023-01-03T01:30:01+00:00"), "bid_price": 98.0, "ask_price": 100.0},
    ]
    bars = quote_rows_to_mid_bars(rows, instrument_id="IF2303.CFFEX", ingested_at=_ts("2026-01-01T00:00:00+00:00"))
    assert len(bars) == 2
    first = bars[0]
    assert first.ts == _ts("2023-01-03T01:29:00+00:00")
    assert first.open == 100.0
    assert first.high == 102.0
    assert first.low == 100.0
    assert first.close == 101.0
    assert first.volume == 3.0
    assert first.trade_count == 3
    assert first.quote_volume == 0.0
    assert first.source == "cffex_quote_mid_bar"
    assert first.bar_source == "quote_mid"
    assert all(value == value for value in (first.open, first.high, first.low, first.close))


def test_quote_volume_policy_zero_and_empty_minutes_skipped():
    rows = [
        {"ts_event": _ts("2023-01-03T01:29:00+00:00"), "bid_price": 100.0, "ask_price": 102.0},
        {"ts_event": _ts("2023-01-03T01:31:00+00:00"), "bid_price": 102.0, "ask_price": 104.0},
    ]
    bars = quote_rows_to_mid_bars(rows, instrument_id="IH2303.CFFEX", volume_policy="zero")
    assert [b.ts.minute for b in bars] == [29, 31]
    assert all(b.volume == 0.0 for b in bars)
    assert all(b.trade_count == 1 for b in bars)


def test_multiple_symbols_are_separate_calls_and_date_partitioning(tmp_path):
    bars_if = quote_rows_to_mid_bars(
        [{"ts_event": _ts("2023-01-03T01:29:00+00:00"), "bid_price": 100.0, "ask_price": 102.0}],
        instrument_id="IF2303.CFFEX",
    )
    bars_ih = quote_rows_to_mid_bars(
        [{"ts_event": _ts("2023-01-04T01:29:00+00:00"), "bid_price": 200.0, "ask_price": 202.0}],
        instrument_id="IH2303.CFFEX",
    )
    paths = write_mid_bars(bars_if + bars_ih, tmp_path / "outputs" / "derived_market_data" / "cffex_mid_bars")
    assert len(paths) == 2
    assert partition_path(
        tmp_path / "outputs" / "derived_market_data" / "cffex_mid_bars",
        exchange="CFFEX",
        venue_type="futures",
        instrument_id="IF2303.CFFEX",
        bar_type="1m",
        date="2023-01-03",
    ) / "part-0.parquet" in paths


def test_output_schema_matches_expected_columns(tmp_path):
    pa = pytest.importorskip("pyarrow.parquet")
    bars = quote_rows_to_mid_bars(
        [{"ts_event": _ts("2023-01-03T01:29:00+00:00"), "bid_price": 100.0, "ask_price": 102.0}],
        instrument_id="IF2303.CFFEX",
    )
    [path] = write_mid_bars(bars, tmp_path / "outputs" / "derived_market_data" / "cffex_mid_bars")
    schema_names = pa.read_schema(path).names
    assert tuple(schema_names) == BAR_COLUMNS


def test_depth_top_of_book_mid_extraction_and_malformed_row():
    rows = [
        {"ts_event": _ts("2023-01-03T01:29:00+00:00"), "bid_price_0": 100.0, "ask_price_0": 102.0},
        {"ts_event": _ts("2023-01-03T01:29:30+00:00"), "bid_price_0": 101.0, "ask_price_0": 103.0},
    ]
    bars = depth_rows_to_mid_bars(rows, instrument_id="IC2303.CFFEX")
    assert len(bars) == 1
    assert bars[0].open == 101.0
    assert bars[0].close == 102.0
    assert bars[0].source == "cffex_depth_mid_bar"
    with pytest.raises(ValueError, match="missing top bid/ask"):
        depth_rows_to_mid_bars([{"ts_event": _ts("2023-01-03T01:29:00+00:00")}], instrument_id="IC2303.CFFEX")
