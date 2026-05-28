"""Volume-weighted momentum.

Definition used here::

    vwm_N = sum_{i=t-N+1..t}(ret_i * volume_i) / sum_{i=t-N+1..t}(volume_i)

This is the rolling volume-weighted average return over a window — a higher
signal when up-moves coincide with heavy volume. Per-symbol state holds the
trailing ``window`` rows of (return, volume), so incremental updates only need
to maintain the rolling sums.
"""
from __future__ import annotations

import polars as pl

from quant_feature_engine.core.feature import Feature, FeatureMeta, PerSymbolMixin
from quant_feature_engine.core.registry import register


@register
class VWM(Feature, PerSymbolMixin):
    """Volume-weighted momentum over ``window`` bars."""

    meta = FeatureMeta(
        name="vwm_20",
        inputs=("symbol", "ts_event", "close", "volume"),
        outputs=("vwm_20",),
        window=20,
        warmup=21,  # one extra for the return diff
        feature_group="volume",
        params={"window": 20},
    )

    def update(self, batch: pl.DataFrame) -> pl.DataFrame:
        window = self.meta.params["window"]

        def _per_symbol(sym: str, sub: pl.DataFrame) -> pl.DataFrame:
            tail: pl.DataFrame | None = self._state.get(sym)
            sub_clean = sub.drop("__qfe_row_idx__")
            combined = (
                pl.concat([tail, sub_clean], how="vertical") if tail is not None else sub_clean
            )

            combined = combined.with_columns(
                (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_ret"),
            )
            combined = combined.with_columns(
                (pl.col("_ret") * pl.col("volume")).alias("_pv"),
            )
            num = pl.col("_pv").rolling_sum(window_size=window)
            den = pl.col("volume").rolling_sum(window_size=window)
            combined = combined.with_columns(
                pl.when(den > 0).then(num / den).otherwise(None).alias("vwm_20")
            )

            new_rows = combined.tail(sub.height).select(["vwm_20"])
            self._state[sym] = combined.drop(["_ret", "_pv", "vwm_20"]).tail(window)
            return new_rows.with_columns(sub["__qfe_row_idx__"])

        return self.process_per_symbol(batch, _per_symbol)
