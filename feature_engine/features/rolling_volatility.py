"""Rolling realised volatility of log returns.

Same rolling-window machinery as SMA but the underlying op is
``rolling_std`` over log returns. We compute log returns inside the feature so
no upstream dependency is required.
"""
from __future__ import annotations

import polars as pl

from feature_engine.core.feature import Feature, FeatureMeta, PerSymbolMixin
from feature_engine.core.registry import register


@register
class RollingVolatility(Feature, PerSymbolMixin):
    """Annualised realised volatility over ``window`` bars."""

    meta = FeatureMeta(
        name="vol_30",
        inputs=("symbol", "ts_event", "close"),
        outputs=("vol_30",),
        window=30,
        warmup=31,  # one extra for the log-return diff
        feature_group="technical",
        params={"window": 30, "annualization": 252.0},
    )

    def update(self, batch: pl.DataFrame) -> pl.DataFrame:
        window = self.meta.params["window"]
        ann = float(self.meta.params["annualization"]) ** 0.5

        def _per_symbol(sym: str, sub: pl.DataFrame) -> pl.DataFrame:
            tail: pl.DataFrame | None = self._state.get(sym)
            sub_clean = sub.drop("__qfe_row_idx__")
            combined = (
                pl.concat([tail, sub_clean], how="vertical") if tail is not None else sub_clean
            )

            combined = combined.with_columns(
                (pl.col("close").log() - pl.col("close").shift(1).log()).alias("_logret")
            )
            combined = combined.with_columns(
                (pl.col("_logret").rolling_std(window_size=window) * ann).alias("vol_30")
            )

            new_rows = combined.tail(sub.height).select(["vol_30"])
            # Keep one more than window so the log-return diff has its lag.
            keep = window
            self._state[sym] = combined.drop(["_logret", "vol_30"]).tail(keep) if keep > 0 else None
            return new_rows.with_columns(sub["__qfe_row_idx__"])

        return self.process_per_symbol(batch, _per_symbol)
