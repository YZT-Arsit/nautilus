"""The parity test.

For every registered feature::

    compute_batch(df)  ==  concat(update(chunk) for chunk in chunks(df))

This is the contract that allows us to share code between offline and live.
If this test fails for a feature, the streaming output for that feature is
*not* a faithful replay of the offline output and the feature is broken.
"""
from __future__ import annotations

import math

import polars as pl
import pytest

from quant_feature_engine.core import registry as _registry


def _chunks(df: pl.DataFrame, size: int):
    for i in range(0, df.height, size):
        yield df.slice(i, size)


def _approx_equal(a: pl.DataFrame, b: pl.DataFrame, tol: float = 1e-9) -> bool:
    """Compare two frames row-by-row with NaN/None awareness."""
    if a.shape != b.shape or list(a.columns) != list(b.columns):
        return False
    for col in a.columns:
        for x, y in zip(a[col].to_list(), b[col].to_list()):
            if x is None and y is None:
                continue
            if x is None or y is None:
                return False
            if isinstance(x, float) and (math.isnan(x) or math.isnan(y)):
                if math.isnan(x) and math.isnan(y):
                    continue
                return False
            if abs(x - y) > tol:
                return False
    return True


@pytest.mark.parametrize(
    "feature_name",
    ["sma_20", "vol_30", "rsi_14", "macd", "vwm_20"],
)
@pytest.mark.parametrize("chunk_size", [1, 7, 50, 500])
def test_parity(feature_name: str, chunk_size: int, synthetic_bars: pl.DataFrame) -> None:
    cls = _registry.get(feature_name)
    f_batch = cls()
    f_stream = cls()

    expected = f_batch.compute_batch(synthetic_bars)

    streamed_pieces: list[pl.DataFrame] = []
    for chunk in _chunks(synthetic_bars, chunk_size):
        streamed_pieces.append(f_stream.update(chunk))
    actual = pl.concat(streamed_pieces, how="vertical")

    assert _approx_equal(expected, actual), (
        f"Batch/streaming divergence in {feature_name} at chunk_size={chunk_size}"
    )
