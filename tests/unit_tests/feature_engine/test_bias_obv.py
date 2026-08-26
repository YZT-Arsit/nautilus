from __future__ import annotations

import pytest

from data_engine.events import BarEvent
from feature_engine.api import bias_spec, obv_spec
from feature_engine.compute.backend import PythonBackend


def bar(index: int, close: float, volume: float = 1.0) -> BarEvent:
    return BarEvent(close, close, close, close, volume, "BTC", index * 60_000_000_000)


def test_bias_uses_completed_close_and_exact_sma_denominator() -> None:
    feature = PythonBackend().create_feature(bias_spec("bias", window=3))
    for index, close in enumerate((10.0, 20.0, 30.0), 1):
        update = feature.update(bar(index, close))
    assert update.value.is_ready
    assert update.value.value == pytest.approx(50.0)


def test_obv_and_obv_sma_share_standard_signed_volume_semantics() -> None:
    obv = PythonBackend().create_feature(obv_spec("obv", window=3, output="obv"))
    mean = PythonBackend().create_feature(obv_spec("mean", window=3, output="sma"))
    values = []
    for index, (close, volume) in enumerate(((10, 2), (11, 3), (10, 5), (10, 7)), 1):
        values.append((obv.update(bar(index, close, volume)), mean.update(bar(index, close, volume))))
    assert values[1][0].value.value == 3.0
    assert values[2][0].value.value == -2.0
    assert values[3][0].value.value == -2.0
    assert values[3][1].value.value == pytest.approx((-2.0 - 2.0 + 3.0) / 3.0)

