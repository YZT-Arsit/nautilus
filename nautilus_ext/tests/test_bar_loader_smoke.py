"""Unit tests for the read-only bar loader smoke (Data Backtest Prep).

Fully offline and pyarrow-free: stats are computed over small in-memory
``BarEvent`` lists. No real cache, no network, no Nautilus, no backtest.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Make the repo root importable so ``scripts`` resolves as a namespace package
# regardless of the active pytest import mode.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.events import BarEvent
from scripts.run_bar_loader_smoke import (
    bars_per_day,
    build_data_config,
    build_parser,
    compute_bar_stats,
    date_range,
    expected_day_count,
    load_range_per_date,
    ns_to_utc_date,
    per_date_configs,
)

_DAY_NS = 86_400_000_000_000
_MIN_NS = 60_000_000_000

# 2024-06-17 00:00:00 UTC in epoch-ns.
_D0 = int(date(2024, 6, 17).toordinal() - date(1970, 1, 1).toordinal()) * _DAY_NS


def _bar(ts_ns, *, close=100.0, volume=1.0, open=None, high=None, low=None):
    return BarEvent(
        close=close,
        open=close if open is None else open,
        high=close if high is None else high,
        low=close if low is None else low,
        volume=volume,
        instrument_id="BTCUSDT.BINANCE",
        event_time_ns=ts_ns,
    )


def _minute_bars(day_ns, n, *, start_minute=0):
    return [_bar(day_ns + (start_minute + i) * _MIN_NS, close=100.0 + i, volume=2.0 + i)
            for i in range(n)]


# ---------------------------------------------------------------------------

def test_bars_per_day_known_and_unknown():
    assert bars_per_day("1m") == 1440
    assert bars_per_day("5m") == 288
    assert bars_per_day("1h") == 24
    assert bars_per_day("nonsense") is None


def test_expected_day_count_and_range_inclusive():
    assert expected_day_count(date(2024, 6, 17), date(2024, 6, 17)) == 1
    assert expected_day_count(date(2024, 6, 17), date(2026, 6, 16)) == 730
    assert date_range(date(2024, 6, 17), date(2024, 6, 19)) == [
        "2024-06-17", "2024-06-18", "2024-06-19"]


def test_ns_to_utc_date():
    assert ns_to_utc_date(_D0) == "2024-06-17"
    assert ns_to_utc_date(_D0 + _DAY_NS) == "2024-06-18"


def test_arg_parsing_and_config():
    args = build_parser().parse_args([
        "--root", "historical_data/market_data", "--exchange", "BINANCE",
        "--venue-type", "spot", "--symbol", "BTCUSDT", "--bar-type", "1m",
        "--start", "2024-06-17", "--end", "2026-06-16"])
    assert args.root == "historical_data/market_data" and args.bar_type == "1m"
    cfg = build_data_config(args)
    assert cfg["mode"] == "hive_parquet_bars"
    assert cfg["filters"] == {"exchange": "BINANCE", "venue_type": "spot",
                              "symbol": "BTCUSDT", "bar_type": "1m"}
    assert cfg["instrument_id"] == "BTCUSDT.BINANCE"  # default <symbol>.<exchange>


def test_compute_stats_full_two_days():
    events = _minute_bars(_D0, 1440) + _minute_bars(_D0 + _DAY_NS, 1440)
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 18), per_day_expected=1440)
    assert s["total_events"] == 2880
    assert s["expected_rows"] == 2 * 1440
    assert s["day_count"] == 2 and s["days_present"] == 2
    assert s["per_day_min"] == 1440 and s["per_day_max"] == 1440
    assert s["monotonic"] is True
    assert s["duplicate_ts"] == 0
    assert s["under_days"] == [] and s["over_days"] == [] and s["missing_days"] == []
    assert s["ohlcv_null_count"] == 0
    assert s["first_ts_ns"] == _D0
    assert s["last_ts_ns"] == _D0 + _DAY_NS + 1439 * _MIN_NS


def test_compute_stats_per_day_anomalies_and_missing():
    # day0: short (2 bars), day1: missing entirely, day2: long (3 bars > expected 2)
    events = _minute_bars(_D0, 2) + _minute_bars(_D0 + 2 * _DAY_NS, 3)
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 19), per_day_expected=2)
    assert s["day_count"] == 3 and s["days_present"] == 2
    assert s["under_days"] == []  # day0 has exactly 2 == expected
    assert s["over_days"] == [("2024-06-19", 3)]
    assert s["missing_days"] == ["2024-06-18"]


def test_compute_stats_under_day():
    events = _minute_bars(_D0, 1439)  # one short of 1440
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 17), per_day_expected=1440)
    assert s["under_days"] == [("2024-06-17", 1439)]
    assert s["over_days"] == []


def test_duplicate_timestamp_count():
    events = [_bar(_D0), _bar(_D0), _bar(_D0 + _MIN_NS)]
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 17), per_day_expected=1440)
    assert s["duplicate_ts"] == 1
    assert s["total_events"] == 3


def test_monotonic_holds_after_internal_sort():
    # input out of order -> stats sorts internally -> monotonic True, order normalised
    events = [_bar(_D0 + 2 * _MIN_NS), _bar(_D0), _bar(_D0 + _MIN_NS)]
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 17), per_day_expected=1440)
    assert s["monotonic"] is True
    assert s["first_ts_ns"] == _D0 and s["last_ts_ns"] == _D0 + 2 * _MIN_NS


def test_ohlcv_null_count():
    events = [_bar(_D0, close=100.0), _bar(_D0 + _MIN_NS)]
    events[1].high = None
    events[1].volume = float("nan")
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 17), per_day_expected=1440)
    assert s["ohlcv_null_count"] == 2  # one None + one NaN


def test_close_and_volume_ranges():
    events = [_bar(_D0, close=100.0, volume=5.0),
              _bar(_D0 + _MIN_NS, close=250.0, volume=1.0)]
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 17), per_day_expected=1440)
    assert s["close_min"] == 100.0 and s["close_max"] == 250.0
    assert s["volume_min"] == 1.0 and s["volume_max"] == 5.0


def test_date_range_filter_excludes_out_of_window():
    # one bar before the window, one inside
    events = [_bar(_D0 - _DAY_NS), _bar(_D0 + _MIN_NS)]
    s = compute_bar_stats(events, date(2024, 6, 17), date(2024, 6, 17), per_day_expected=1440)
    assert s["total_events"] == 1


def test_physical_date_filter_default_on_and_fallback():
    base = ["--root", "r", "--start", "2026-06-14", "--end", "2026-06-16"]
    assert build_parser().parse_args(base).physical_date_filter is True  # default on
    assert build_parser().parse_args(
        base + ["--no-physical-date-filter"]).physical_date_filter is False


def test_per_date_configs_adds_date_filter():
    base = build_data_config(build_parser().parse_args(
        ["--root", "r", "--start", "2026-06-14", "--end", "2026-06-16"]))
    pairs = list(per_date_configs(base, ["2026-06-14", "2026-06-15"]))
    assert [d for d, _ in pairs] == ["2026-06-14", "2026-06-15"]
    assert pairs[0][1]["filters"]["date"] == "2026-06-14"
    assert pairs[1][1]["filters"]["date"] == "2026-06-15"
    # base config is not mutated (still has no date)
    assert "date" not in base["filters"]


class _MockLoader:
    """Records per-date calls; returns canned events or simulates missing/empty."""

    def __init__(self, per_date_events, *, missing=(), empty=()):
        self._per_date = per_date_events
        self._missing = set(missing)
        self._empty = set(empty)
        self.calls = []

    def __call__(self, cfg):
        d = cfg["filters"]["date"]
        self.calls.append(d)
        if d in self._missing:
            raise ValueError(
                f"no parquet fragments under {cfg['root']!r} match filters {cfg['filters']!r}")
        if d in self._empty:
            return [], []
        return [], list(self._per_date.get(d, []))


def test_load_range_per_date_calls_once_per_date_and_merges_sorted():
    days = ["2026-06-14", "2026-06-15", "2026-06-16"]
    per_date = {
        "2026-06-14": _minute_bars(_D0, 3),
        "2026-06-15": _minute_bars(_D0 + _DAY_NS, 3),
        "2026-06-16": _minute_bars(_D0 + 2 * _DAY_NS, 3),
    }
    loader = _MockLoader(per_date)
    base = build_data_config(build_parser().parse_args(
        ["--root", "r", "--start", days[0], "--end", days[-1]]))
    events, loaded, missing = load_range_per_date(base, days, load_fn=loader)
    assert loader.calls == days                    # one call per date, in order
    assert len(events) == 9 and loaded == days and missing == []
    # merged result is globally sorted by event_time_ns
    assert [e.event_time_ns for e in events] == sorted(e.event_time_ns for e in events)


def test_load_range_per_date_reports_missing_partition_and_empty():
    days = ["2026-06-14", "2026-06-15", "2026-06-16"]
    per_date = {"2026-06-14": _minute_bars(_D0, 2)}
    loader = _MockLoader(per_date, missing=["2026-06-15"], empty=["2026-06-16"])
    base = build_data_config(build_parser().parse_args(
        ["--root", "r", "--start", days[0], "--end", days[-1]]))
    events, loaded, missing = load_range_per_date(base, days, load_fn=loader)
    assert loader.calls == days
    assert loaded == ["2026-06-14"]
    assert missing == ["2026-06-15", "2026-06-16"]  # absent partition + empty day
    assert len(events) == 2


def test_load_range_per_date_reraises_unexpected_error():
    def boom(cfg):
        raise ValueError("schema guard: required close column 'close' is missing")

    base = build_data_config(build_parser().parse_args(
        ["--root", "r", "--start", "2026-06-14", "--end", "2026-06-14"]))
    try:
        load_range_per_date(base, ["2026-06-14"], load_fn=boom)
    except ValueError as exc:
        assert "close column" in str(exc)  # non-missing-partition error propagates
    else:
        raise AssertionError("expected the unexpected error to propagate")


def test_source_scan_no_nautilus_network_or_trading():
    import inspect

    from scripts import run_bar_loader_smoke

    src = inspect.getsource(run_bar_loader_smoke)
    assert "import nautilus_trader" not in src
    assert "from nautilus_trader" not in src
    for net in ("import websocket", "import websockets", "import asyncio",
                "import aiohttp", "import urllib", "import requests", "import socket"):
        assert net not in src, f"unexpected network import: {net}"
    for forbidden in ("api_key", "apiKey", "secret", "signature", "place_order",
                      "new_order", "cancel_order", "/api/v3/order", "/sapi/"):
        assert forbidden not in src, f"unexpected trading reference: {forbidden}"
