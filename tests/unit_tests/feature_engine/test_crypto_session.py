from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data_engine.events import BarEvent
from feature_engine.api import (
    SpecFeatureEngine,
    completed_timeframe_spec,
    crypto_utc_session_spec,
    session_flatten_due_spec,
)


MINUTE = 60_000_000_000


def ns(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1_000_000_000)


def bar(end: str, *, open_: float, high: float, low: float, close: float,
        volume: float = 1.0, quote_volume: float | None = None) -> BarEvent:
    return BarEvent(
        open=open_, high=high, low=low, close=close, volume=volume,
        quote_volume=quote_volume, instrument_id="BTCUSDT-PERP.BINANCE",
        event_time_ns=ns(end),
    )


def test_session_vwap_preserves_quote_volume_and_previous_day_has_no_lookahead() -> None:
    engine = SpecFeatureEngine([
        crypto_utc_session_spec("vwap", output="session_vwap"),
        crypto_utc_session_spec("pdh", output="previous_high"),
        crypto_utc_session_spec("pdc", output="previous_close"),
    ], stamp_process_time=False)
    first = engine.on_event(bar(
        "2025-01-03T00:01:00", open_=100, high=101, low=99, close=100,
        volume=3, quote_volume=301,
    ))
    assert first.value("vwap") == pytest.approx(301 / 3)
    assert first.value("pdh") is None
    engine.on_event(bar(
        "2025-01-04T00:00:00", open_=100, high=120, low=90, close=110,
        volume=2, quote_volume=219,
    ))
    next_day = engine.on_event(bar(
        "2025-01-04T00:02:00", open_=111, high=112, low=108, close=109,
        volume=4, quote_volume=440,
    ))
    assert next_day.value("pdh") == 120
    assert next_day.value("pdc") == 110
    assert next_day.value("vwap") == 110


def test_missing_midnight_bar_uses_first_real_observation_as_session_open() -> None:
    engine = SpecFeatureEngine([
        crypto_utc_session_spec("open", output="session_open"),
        crypto_utc_session_spec("open_ts", output="session_open_time_ns"),
    ], stamp_process_time=False)
    snapshot = engine.on_event(bar(
        "2025-01-03T00:06:00", open_=101, high=103, low=100, close=102,
    ))
    assert snapshot.value("open") == 101
    assert snapshot.value("open_ts") == ns("2025-01-03T00:05:00")


def test_opening_range_is_unavailable_until_window_completion() -> None:
    engine = SpecFeatureEngine([
        crypto_utc_session_spec("or_high", output="opening_range_high", opening_range_minutes=30),
        crypto_utc_session_spec("or_ready", output="opening_range_ready", opening_range_minutes=30),
    ], stamp_process_time=False)
    before = engine.on_event(bar(
        "2025-01-03T00:15:00", open_=100, high=105, low=99, close=103,
    ))
    assert before.value("or_high") == 105
    assert before.value("or_ready") is False
    complete = engine.on_event(bar(
        "2025-01-03T00:30:00", open_=103, high=108, low=102, close=107,
    ))
    assert complete.value("or_high") == 108
    assert complete.value("or_ready") is True


def test_session_flatten_due_respects_lag_without_synthetic_midnight_fill() -> None:
    lag0 = SpecFeatureEngine([
        session_flatten_due_spec("flatten", execution_lag_minutes=0),
    ], stamp_process_time=False)
    lag1 = SpecFeatureEngine([
        session_flatten_due_spec("flatten", execution_lag_minutes=1),
    ], stamp_process_time=False)
    event_2358 = bar("2025-01-03T23:58:00", open_=100, high=100, low=100, close=100)
    event_2359 = bar("2025-01-03T23:59:00", open_=100, high=100, low=100, close=100)
    assert lag0.on_event(event_2358).value("flatten") is False
    assert lag0.on_event(event_2359).value("flatten") is True
    assert lag1.on_event(event_2358).value("flatten") is True
    assert lag1.on_event(event_2359).value("flatten") is False


def test_session_entries_stay_disabled_after_flatten_decision_until_new_session() -> None:
    lag0 = SpecFeatureEngine([
        crypto_utc_session_spec(
            "allowed", output="session_entry_allowed", execution_lag_minutes=0,
        ),
    ], stamp_process_time=False)
    lag1 = SpecFeatureEngine([
        crypto_utc_session_spec(
            "allowed", output="session_entry_allowed", execution_lag_minutes=1,
        ),
    ], stamp_process_time=False)
    event_2358 = bar("2025-01-03T23:58:00", open_=100, high=100, low=100, close=100)
    event_2359 = bar("2025-01-03T23:59:00", open_=100, high=100, low=100, close=100)
    event_0000 = bar("2025-01-04T00:00:00", open_=100, high=100, low=100, close=100)
    event_0001 = bar("2025-01-04T00:01:00", open_=100, high=100, low=100, close=100)
    assert lag0.on_event(event_2358).value("allowed") is True
    assert lag0.on_event(event_2359).value("allowed") is False
    assert lag1.on_event(event_2358).value("allowed") is False
    assert lag1.on_event(event_2359).value("allowed") is False
    assert lag0.on_event(event_0000).value("allowed") is False
    assert lag1.on_event(event_0000).value("allowed") is False
    assert lag0.on_event(event_0001).value("allowed") is True
    assert lag1.on_event(event_0001).value("allowed") is True


def test_completed_timeframe_sma_does_not_see_incomplete_bar() -> None:
    engine = SpecFeatureEngine([
        completed_timeframe_spec("ma", timeframe_minutes=5, output="sma", window=2),
    ], stamp_process_time=False)
    for minute in range(1, 5):
        snap = engine.on_event(bar(
            f"2025-01-03T00:0{minute}:00", open_=100, high=100, low=100, close=100,
        ))
        assert snap.value("ma") is None
    assert engine.on_event(bar(
        "2025-01-03T00:05:00", open_=100, high=100, low=100, close=100,
    )).value("ma") is None
    for minute in range(6, 10):
        assert engine.on_event(bar(
            f"2025-01-03T00:0{minute}:00", open_=110, high=110, low=110, close=110,
        )).value("ma") is None
    assert engine.on_event(bar(
        "2025-01-03T00:10:00", open_=110, high=110, low=110, close=110,
    )).value("ma") == pytest.approx(105)


def test_utc_session_is_dst_immune() -> None:
    for date in ("2025-03-09", "2025-03-30", "2025-11-02"):
        start = ns(f"{date}T00:00:00")
        assert (start + 86_400_000_000_000) - start == 86_400_000_000_000
