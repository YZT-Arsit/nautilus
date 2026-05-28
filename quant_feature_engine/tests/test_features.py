"""Smoke tests for each registered feature."""
from __future__ import annotations

import polars as pl
import pytest

from quant_feature_engine.core import registry as _registry


@pytest.mark.parametrize(
    "feature_name",
    ["sma_20", "vol_30", "rsi_14", "macd", "vwm_20"],
)
def test_feature_runs_and_emits_right_columns(
    feature_name: str, synthetic_bars: pl.DataFrame
) -> None:
    cls = _registry.get(feature_name)
    f = cls()
    out = f.compute_batch(synthetic_bars)
    assert out.height == synthetic_bars.height
    assert list(out.columns) == list(cls.meta.outputs)


def test_derived_feature_runs(synthetic_bars: pl.DataFrame) -> None:
    """vwm_zscore_60 needs vwm_20 in its input frame — simulate that."""
    vwm = _registry.get("vwm_20")()
    z = _registry.get("vwm_zscore_60")()
    df = synthetic_bars.hstack(vwm.compute_batch(synthetic_bars))
    out = z.compute_batch(df)
    assert out.height == df.height
    assert "vwm_zscore_60" in out.columns


def test_snapshot_round_trip(synthetic_bars: pl.DataFrame) -> None:
    """Snapshot mid-stream → restore → produces identical subsequent outputs."""
    f1 = _registry.get("sma_20")()
    f2 = _registry.get("sma_20")()

    first_half = synthetic_bars.head(synthetic_bars.height // 2)
    second_half = synthetic_bars.tail(synthetic_bars.height - synthetic_bars.height // 2)

    f1.update(first_half)
    blob = f1.snapshot()
    f2.restore(blob)

    expected = f1.update(second_half)
    actual = f2.update(second_half)
    assert expected.equals(actual)
