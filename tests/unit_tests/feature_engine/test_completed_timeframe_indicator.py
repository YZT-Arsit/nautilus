from __future__ import annotations

from data_engine.events import BarEvent
from feature_engine.builders import completed_timeframe_spec
from feature_engine.compute.backend import PythonBackend


NS = 1_000_000_000


def bar(minute: int, close: float) -> BarEvent:
    return BarEvent(close, close, close, close, 1.0, "BTCUSDT", minute * 60 * NS)


def test_completed_timeframe_child_indicator_never_sees_incomplete_bar() -> None:
    feature = PythonBackend().create_feature(completed_timeframe_spec(
        "mtf_ema", timeframe_minutes=5, output="value", indicator="ema",
        indicator_params={"window": 2},
    ))
    for minute, close in ((1, 10.0), (2, 20.0), (3, 30.0), (4, 40.0)):
        update = feature.update(bar(minute, close))
        assert not update.value.is_ready
    # 5m is the first completed child observation; a 2-child EMA is still warmup.
    assert not feature.update(bar(5, 50.0)).value.is_ready
    for minute, close in ((6, 100.0), (7, 200.0), (8, 300.0), (9, 400.0)):
        assert not feature.update(bar(minute, close)).value.is_ready
    completed = feature.update(bar(10, 500.0)).value
    assert completed.is_ready
    assert completed.value == 275.0  # SMA seed of child closes 50 and 500.


def test_completed_timeframe_state_round_trip_preserves_child_indicator() -> None:
    spec = completed_timeframe_spec("mtf", timeframe_minutes=5, output="value", indicator="rsi", indicator_params={"window": 2})
    first = PythonBackend().create_feature(spec)
    for minute in range(1, 11):
        first.update(bar(minute, float(minute)))
    second = PythonBackend().create_feature(spec); second.load_state_dict(first.state_dict())
    assert second.state_dict() == first.state_dict()
