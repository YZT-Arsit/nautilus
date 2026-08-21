import math

from data_engine.events import BarEvent
from feature_engine.api import (
    SpecFeatureEngine, aroon_spec, awesome_oscillator_spec, confirmed_fractal_spec,
    ema_spec, macd_spec, rsi_spec,
    supertrend_spec,
)


def bar(close: float, index: int, *, high: float | None = None, low: float | None = None) -> BarEvent:
    return BarEvent(
        close=close, open=close, high=close if high is None else high,
        low=close if low is None else low, volume=1.0,
        instrument_id="BTCUSDT-PERP.BINANCE", event_time_ns=index,
    )


def test_ema_uses_sma_seed_then_standard_recursive_update() -> None:
    engine = SpecFeatureEngine([ema_spec("ema", window=3)], stamp_process_time=False)
    values = [engine.on_event(bar(value, index)).value("ema") for index, value in enumerate((1, 2, 3, 4), 1)]
    assert values[:2] == [None, None]
    assert values[2] == 2.0
    assert values[3] == 3.0


def test_wilder_rsi_is_ready_only_after_window_price_changes() -> None:
    engine = SpecFeatureEngine([rsi_spec("rsi", window=3)], stamp_process_time=False)
    values = [engine.on_event(bar(value, index)).value("rsi") for index, value in enumerate((1, 2, 3, 4), 1)]
    assert values[:3] == [None, None, None]
    assert values[3] == 100.0


def test_awesome_oscillator_uses_source_hl2_without_future_bars() -> None:
    engine = SpecFeatureEngine(
        [awesome_oscillator_spec("ao", fast_window=2, slow_window=3)],
        stamp_process_time=False,
    )
    first = engine.on_event(bar(1, 1, high=2, low=0)).value("ao")
    second = engine.on_event(bar(2, 2, high=3, low=1)).value("ao")
    third = engine.on_event(bar(3, 3, high=4, low=2)).value("ao")
    assert first is None and second is None
    assert math.isclose(third, 0.5)


def test_aroon_uses_last_occurrence_and_completed_window_only() -> None:
    engine = SpecFeatureEngine(
        [aroon_spec("up", window=3, output="up"), aroon_spec("down", window=3, output="down")],
        stamp_process_time=False,
    )
    snapshots = [engine.on_event(bar(v, i, high=h, low=l)) for i, (v, h, l) in enumerate(((2, 3, 1), (3, 4, 2), (4, 5, 0)), 1)]
    assert snapshots[1].value("up") is None
    assert snapshots[2].value("up") == 100.0
    assert snapshots[2].value("down") == 100.0


def test_macd_has_deterministic_seed_and_signal_warmup() -> None:
    engine = SpecFeatureEngine(
        [macd_spec("dif", fast_window=2, slow_window=3, signal_window=2, output="dif"),
         macd_spec("signal", fast_window=2, slow_window=3, signal_window=2, output="signal")],
        stamp_process_time=False,
    )
    snapshots = [engine.on_event(bar(float(value), value)) for value in range(1, 5)]
    assert snapshots[1].value("dif") is None
    assert math.isclose(snapshots[2].value("dif"), 0.5)
    assert snapshots[2].value("signal") is None
    assert math.isclose(snapshots[3].value("signal"), 0.5)


def test_fractal_is_revealed_only_after_two_right_hand_bars() -> None:
    engine = SpecFeatureEngine(
        [confirmed_fractal_spec("upper", output="upper"),
         confirmed_fractal_spec("pulse", output="upper_pulse")],
        stamp_process_time=False,
    )
    highs = (1.0, 2.0, 5.0, 3.0, 2.0)
    snapshots = [engine.on_event(bar(h, i, high=h, low=0.0)) for i, h in enumerate(highs, 1)]
    assert all(snapshot.value("upper") is None for snapshot in snapshots[:4])
    assert snapshots[4].value("upper") == 5.0
    assert snapshots[4].value("pulse") == 1.0


def test_supertrend_is_warmed_on_completed_bars_and_emits_signed_direction() -> None:
    engine = SpecFeatureEngine(
        [supertrend_spec("st", window=3, multiplier=1.0, output="direction")],
        stamp_process_time=False,
    )
    values = [
        engine.on_event(bar(close, index, high=close + 0.5, low=close - 0.5)).value("st")
        for index, close in enumerate((10.0, 10.0, 10.0, 13.0), 1)
    ]
    assert values[:2] == [None, None]
    assert values[2] in {-1.0, 1.0}
    assert values[3] == 1.0
