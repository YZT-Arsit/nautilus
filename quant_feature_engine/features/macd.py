"""MACD = EMA(fast) - EMA(slow), signal = EMA(MACD), hist = MACD - signal.

A multi-output feature: ``outputs = ('macd', 'macd_signal', 'macd_hist')``.
Each output column is a recursive EMA so the per-symbol state is just
``(ema_fast, ema_slow, signal)``.
"""
from __future__ import annotations

import polars as pl

from quant_feature_engine.core.feature import Feature, FeatureMeta, PerSymbolMixin
from quant_feature_engine.core.registry import register


def _ema_step(prev: float | None, x: float, alpha: float) -> float:
    """One step of an EMA. Seeded with the first observation."""
    return x if prev is None else alpha * x + (1 - alpha) * prev


@register
class MACD(Feature, PerSymbolMixin):
    """12/26/9 MACD on ``close``."""

    meta = FeatureMeta(
        name="macd",
        inputs=("symbol", "ts_event", "close"),
        outputs=("macd", "macd_signal", "macd_hist"),
        window=26,
        warmup=26,
        feature_group="technical",
        params={"fast": 12, "slow": 26, "signal": 9},
    )

    def update(self, batch: pl.DataFrame) -> pl.DataFrame:
        alpha_fast = 2.0 / (self.meta.params["fast"] + 1)
        alpha_slow = 2.0 / (self.meta.params["slow"] + 1)
        alpha_sig = 2.0 / (self.meta.params["signal"] + 1)

        def _per_symbol(sym: str, sub: pl.DataFrame) -> pl.DataFrame:
            state = self._state.setdefault(
                sym, {"ema_fast": None, "ema_slow": None, "signal": None}
            )
            macd_out: list[float | None] = []
            sig_out: list[float | None] = []
            hist_out: list[float | None] = []

            for c in sub["close"].to_list():
                state["ema_fast"] = _ema_step(state["ema_fast"], c, alpha_fast)
                state["ema_slow"] = _ema_step(state["ema_slow"], c, alpha_slow)
                macd = state["ema_fast"] - state["ema_slow"]
                state["signal"] = _ema_step(state["signal"], macd, alpha_sig)
                macd_out.append(macd)
                sig_out.append(state["signal"])
                hist_out.append(macd - state["signal"])

            # Pin dtypes so an all-null first chunk doesn't poison concat.
            df = pl.DataFrame(
                {
                    "macd": pl.Series("macd", macd_out, dtype=pl.Float64),
                    "macd_signal": pl.Series("macd_signal", sig_out, dtype=pl.Float64),
                    "macd_hist": pl.Series("macd_hist", hist_out, dtype=pl.Float64),
                }
            )
            return df.with_columns(sub["__qfe_row_idx__"])

        return self.process_per_symbol(batch, _per_symbol)
