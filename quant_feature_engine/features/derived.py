"""Derived feature: depends on a previously computed feature.

This example computes the z-score of :class:`VWM` over a window — it consumes
the ``vwm_20`` column produced by :class:`VWM`, which the DAG resolver
guarantees is present in the batch by the time ``update`` is called.
"""
from __future__ import annotations

import polars as pl

from quant_feature_engine.core.feature import Feature, FeatureMeta, PerSymbolMixin
from quant_feature_engine.core.registry import register


@register
class VWMZScore(Feature, PerSymbolMixin):
    """Rolling z-score of ``vwm_20``."""

    meta = FeatureMeta(
        name="vwm_zscore_60",
        inputs=("symbol", "ts_event", "vwm_20"),
        outputs=("vwm_zscore_60",),
        dependencies=("vwm_20",),
        window=60,
        warmup=80,  # 20 (vwm) + 60 (z window)
        feature_group="volume",
        params={"window": 60, "source": "vwm_20"},
    )

    def update(self, batch: pl.DataFrame) -> pl.DataFrame:
        window = self.meta.params["window"]
        src = self.meta.params["source"]

        def _per_symbol(sym: str, sub: pl.DataFrame) -> pl.DataFrame:
            tail: pl.DataFrame | None = self._state.get(sym)
            sub_clean = sub.drop("__qfe_row_idx__")
            combined = (
                pl.concat([tail, sub_clean], how="vertical") if tail is not None else sub_clean
            )

            mean = pl.col(src).rolling_mean(window_size=window)
            std = pl.col(src).rolling_std(window_size=window)
            combined = combined.with_columns(
                pl.when(std > 0)
                .then((pl.col(src) - mean) / std)
                .otherwise(None)
                .alias("vwm_zscore_60")
            )

            new_rows = combined.tail(sub.height).select(["vwm_zscore_60"])
            self._state[sym] = combined.drop("vwm_zscore_60").tail(window - 1)
            return new_rows.with_columns(sub["__qfe_row_idx__"])

        return self.process_per_symbol(batch, _per_symbol)
