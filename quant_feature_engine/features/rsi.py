"""Relative Strength Index (Wilder smoothing).

State per symbol = ``(prev_close, avg_gain, avg_loss, bars_seen)``. Wilder's
smoothing is just an EMA with ``alpha = 1/period``, which we can implement
recursively so each new bar costs O(1).
"""
from __future__ import annotations

import polars as pl

from quant_feature_engine.core.feature import Feature, FeatureMeta, PerSymbolMixin
from quant_feature_engine.core.registry import register


@register
class RSI(Feature, PerSymbolMixin):
    """Classic 14-bar RSI."""

    meta = FeatureMeta(
        name="rsi_14",
        inputs=("symbol", "ts_event", "close"),
        outputs=("rsi_14",),
        window=14,
        warmup=14,
        feature_group="technical",
        params={"period": 14},
    )

    def update(self, batch: pl.DataFrame) -> pl.DataFrame:
        period = int(self.meta.params["period"])

        def _per_symbol(sym: str, sub: pl.DataFrame) -> pl.DataFrame:
            state = self._state.setdefault(
                sym,
                {"prev_close": None, "avg_gain": 0.0, "avg_loss": 0.0, "n": 0},
            )

            closes = sub["close"].to_list()
            out: list[float | None] = []
            for c in closes:
                if state["prev_close"] is None:
                    out.append(None)
                    state["prev_close"] = c
                    continue
                change = c - state["prev_close"]
                gain = max(change, 0.0)
                loss = max(-change, 0.0)
                state["n"] += 1
                if state["n"] < period:
                    # Wilder seeds with a simple average of the first `period` gains/losses.
                    state["avg_gain"] += gain
                    state["avg_loss"] += loss
                    out.append(None)
                elif state["n"] == period:
                    state["avg_gain"] = (state["avg_gain"] + gain) / period
                    state["avg_loss"] = (state["avg_loss"] + loss) / period
                    out.append(self._rsi(state))
                else:
                    state["avg_gain"] = (state["avg_gain"] * (period - 1) + gain) / period
                    state["avg_loss"] = (state["avg_loss"] * (period - 1) + loss) / period
                    out.append(self._rsi(state))
                state["prev_close"] = c

            # Pin dtype explicitly; otherwise an all-null warm-up chunk gets
            # Polars' ``Null`` dtype and later concats break with a SchemaError.
            df = pl.DataFrame({"rsi_14": pl.Series("rsi_14", out, dtype=pl.Float64)})
            return df.with_columns(sub["__qfe_row_idx__"])

        return self.process_per_symbol(batch, _per_symbol)

    @staticmethod
    def _rsi(state: dict) -> float:
        loss = state["avg_loss"]
        if loss == 0:
            return 100.0
        rs = state["avg_gain"] / loss
        return 100.0 - 100.0 / (1.0 + rs)
