"""Simple moving average.

Demonstrates the rolling-window pattern:

  * State per symbol = the trailing ``window-1`` rows of the input column.
  * ``update(batch)`` prepends that tail to the batch, computes Polars
    ``rolling_mean``, slices the tail back off, and saves a fresh tail.

Because the operation is deterministic on the *concatenation* of prior tail +
new batch, feeding the whole history in one shot or one row at a time
produces byte-identical output. This is the parity guarantee tested in
:mod:`tests.test_streaming_batch_parity`.
"""
from __future__ import annotations

import polars as pl

from feature_engine.core.feature import Feature, FeatureMeta, PerSymbolMixin
from feature_engine.core.registry import register


@register
class SMA(Feature, PerSymbolMixin):
    """Simple moving average of ``close``."""

    meta = FeatureMeta(
        name="sma_20",
        inputs=("symbol", "ts_event", "close"),
        outputs=("sma_20",),
        window=20,
        warmup=20,
        feature_group="technical",
        params={"window": 20, "column": "close"},
    )

    def update(self, batch: pl.DataFrame) -> pl.DataFrame:
        window = self.meta.params["window"]
        col = self.meta.params["column"]

        def _per_symbol(sym: str, sub: pl.DataFrame) -> pl.DataFrame:
            tail: pl.DataFrame | None = self._state.get(sym)
            if tail is not None:
                combined = pl.concat([tail, sub.drop("__qfe_row_idx__")], how="vertical")
            else:
                combined = sub.drop("__qfe_row_idx__")

            rolled = combined.with_columns(
                pl.col(col).rolling_mean(window_size=window).alias("sma_20")
            )
            new_rows = rolled.tail(sub.height).select(["sma_20"])
            keep = window - 1
            self._state[sym] = combined.tail(keep) if keep > 0 else None
            return new_rows.with_columns(sub["__qfe_row_idx__"])

        return self.process_per_symbol(batch, _per_symbol)
