import math

from data_engine.events import BarEvent
from feature_engine.api import (
    SpecFeatureEngine, adx_spec, cci_spec, hlc_mean_spec, hma_spec,
    minus_di_spec, plus_di_spec,
)


def bar(value: float, index: int) -> BarEvent:
    return BarEvent(
        close=value, open=value, high=value, low=value, volume=1.0,
        instrument_id="BTCUSDT-PERP.BINANCE", event_time_ns=index,
    )


def test_hma_matches_weighted_definition_without_lookahead() -> None:
    engine = SpecFeatureEngine([hma_spec("hma", window=4)], stamp_process_time=False)
    values = []
    for index, value in enumerate((1, 2, 3, 4, 5), start=1):
        values.append(engine.on_event(bar(value, index)).value("hma"))
    assert values[:4] == [None, None, None, None]
    assert math.isclose(values[4], 5.0)


def test_cci_and_hlc_mean_use_hlc3_source_values() -> None:
    engine = SpecFeatureEngine(
        [cci_spec("cci", window=3), hlc_mean_spec("hlc", window=3)],
        stamp_process_time=False,
    )
    snapshot = None
    for index, value in enumerate((1, 2, 3), start=1):
        snapshot = engine.on_event(bar(value, index))
    assert snapshot is not None
    assert math.isclose(snapshot.value("hlc"), 2.0)
    assert math.isclose(snapshot.value("cci"), 100.0)


def test_wilder_directional_movement_has_deterministic_trend_values() -> None:
    engine = SpecFeatureEngine(
        [adx_spec("adx", window=3), plus_di_spec("plus", window=3), minus_di_spec("minus", window=3)],
        stamp_process_time=False,
    )
    snapshots = [engine.on_event(bar(float(value), value)) for value in range(1, 7)]
    assert snapshots[3].value("adx") is None
    assert math.isclose(snapshots[4].value("adx"), 100.0)
    assert snapshots[4].value("plus") > 0.0
    assert math.isclose(snapshots[4].value("minus"), 0.0)
