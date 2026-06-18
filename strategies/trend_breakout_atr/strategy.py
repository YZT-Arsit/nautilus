"""Trend-breakout + ATR strategy plugin (low-turnover, position-aware).

A deliberately simple, explainable counterpart to the VWM baseline:

* **Trend filter** - only take longs above a slow MA, shorts below it.
* **Breakout confirmation** - enter long on a close above the *previous* rolling
  high, short below the *previous* rolling low. Using the **previous** bar's
  rolling high/low (the window strictly before the current bar) avoids any
  look-ahead.
* **ATR risk** - a hard ATR stop and an ATR give-back (trailing) exit.
* **Cooldown** - after any close, no new entry for ``cooldown_bars`` bars. This,
  plus "close-only, never reverse in one bar", keeps turnover low and avoids the
  one-minute flip-flop / partial-fill fragmentation seen in VWM.

Signal contract (BUY / SELL / HOLD only - the signal->order decision stays in
``SignalToOrderPolicy`` with ``sell_means: short`` and a NETTING account):

* flat + long breakout  -> ``BUY``  (opens long)
* flat + short breakout -> ``SELL`` (opens short)
* long + exit           -> ``SELL`` (closes long -> flat)
* short + exit          -> ``BUY``  (closes short -> flat)

Because the strategy is position-aware and only ever moves the net position by
one unit (open-from-flat or close-to-flat, never both in one bar), the existing
policy + native NETTING backend handle it with **no new signal type required**.

This module imports **no** ``nautilus_trader`` and no pandas: the indicator maths
are plain-Python rolling windows, so the whole engine is unit-testable offline.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from feature_engine.api import FeatureSnapshot, FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

# Identity passthrough feature names (window=1 rolling mean == the raw field):
# the runner hands the strategy a FeatureSnapshot, so we expose raw OHLC this way
# and compute trend/breakout/ATR inside the engine (full look-ahead control).
_CLOSE = "tba_bar_close"
_HIGH = "tba_bar_high"
_LOW = "tba_bar_low"


@dataclass(frozen=True)
class TrendBreakoutAtrConfig:
    """User-facing parameters for the trend-breakout + ATR strategy."""

    trend_len: int = 120        # slow MA for the trend filter (~2h of 1m bars)
    breakout_len: int = 60      # rolling high/low window (~1h)
    atr_len: int = 30           # ATR window
    atr_mult_stop: float = 2.0  # hard stop = entry -/+ atr_mult_stop * ATR
    atr_mult_exit: float = 1.0  # give-back exit from best favourable close
    cooldown_bars: int = 30     # bars to wait flat after any close before re-entry
    min_atr_pct: float = 0.0005  # volatility filter: ATR/close must be >= this
    allow_short: bool = True
    quantity: float = 1.0       # informational; sizing lives in execution config
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None


def build_specs(config: TrendBreakoutAtrConfig) -> list[FeatureSpec]:
    """Passthrough OHLC specs (close/high/low) consumed by ``on_snapshot``."""
    passthrough = {"input_type": "bar", "window": 1}
    return [
        rolling_mean_spec(_CLOSE, input_field="close", **passthrough),
        rolling_mean_spec(_HIGH, input_field="high", **passthrough),
        rolling_mean_spec(_LOW, input_field="low", **passthrough),
    ]


class TrendBreakoutAtrEngine:
    """Pure, position-aware decision engine (no Nautilus, no pandas).

    Feed one completed bar at a time via :meth:`update`; returns ``(signal,
    reason)``. Tracks position (flat/long/short), entry/best price, cooldown, and
    rolling buffers. The breakout reference is the **previous** bar's rolling
    high/low, so a bar's own high/low never triggers its own breakout.
    """

    def __init__(self, config: TrendBreakoutAtrConfig) -> None:
        self.cfg = config
        self.position = 0           # -1 short, 0 flat, +1 long
        self.entry_price: float | None = None
        self.best_price: float | None = None  # best favourable close since entry
        self.cooldown_remaining = 0
        self.bars_since_entry = 0
        self._prev_close: float | None = None
        self._highs: deque[float] = deque(maxlen=config.breakout_len)  # prior bars
        self._lows: deque[float] = deque(maxlen=config.breakout_len)
        self._closes: deque[float] = deque(maxlen=config.trend_len)
        self._trs: deque[float] = deque(maxlen=config.atr_len)

    # -- indicators ----------------------------------------------------------

    def _true_range(self, high: float, low: float) -> float:
        if self._prev_close is None:
            return high - low
        pc = self._prev_close
        return max(high - low, abs(high - pc), abs(low - pc))

    def update(self, close: float, high: float, low: float) -> tuple[str, str]:
        cfg = self.cfg
        # 1. breakout reference from PRIOR bars only (computed before append).
        prev_upper = max(self._highs) if len(self._highs) == cfg.breakout_len else None
        prev_lower = min(self._lows) if len(self._lows) == cfg.breakout_len else None
        # 2. ATR over completed bars (includes the current, never the future).
        tr = self._true_range(high, low)
        self._trs.append(tr)
        atr = (sum(self._trs) / len(self._trs)) if len(self._trs) == cfg.atr_len else None
        # 3. trend MA over closes including the current (a filter on the acted price).
        self._closes.append(close)
        trend_ma = (sum(self._closes) / len(self._closes)) if len(self._closes) == cfg.trend_len else None

        signal, reason = self._decide(close, prev_upper, prev_lower, trend_ma, atr)

        # 4. roll the breakout buffers AFTER the decision (no look-ahead) + advance.
        self._highs.append(high)
        self._lows.append(low)
        self._prev_close = close
        if self.position != 0:
            self.bars_since_entry += 1
        return signal, reason

    # -- decision (position-aware) ------------------------------------------

    def _decide(self, close, prev_upper, prev_lower, trend_ma, atr) -> tuple[str, str]:
        cfg = self.cfg
        ready = (prev_upper is not None and prev_lower is not None
                 and trend_ma is not None and atr is not None)
        if not ready:
            return HOLD, "warmup_hold"

        vol_ok = close > 0 and (atr / close) >= cfg.min_atr_pct

        if self.position == 0:
            if self.cooldown_remaining > 0:
                self.cooldown_remaining -= 1
                return HOLD, "cooldown_hold"
            up_break = close > prev_upper
            dn_break = close < prev_lower
            if up_break and dn_break:
                # Only possible with an inverted band (prev_upper < prev_lower),
                # i.e. corrupt/degenerate input - never act on it.
                return HOLD, "ambiguous_hold"
            long_sig = up_break and close > trend_ma and vol_ok
            short_sig = cfg.allow_short and dn_break and close < trend_ma and vol_ok
            if long_sig:
                self._open(+1, close)
                return BUY, "long_breakout"
            if short_sig:
                self._open(-1, close)
                return SELL, "short_breakout"
            if not vol_ok:
                return HOLD, "low_volatility_hold"
            return HOLD, "no_signal"

        if self.position == 1:  # long
            self.best_price = max(self.best_price, close)
            reason = self._long_exit(close, prev_lower, trend_ma, atr)
            if reason:
                self._close()
                return SELL, reason
            return HOLD, "hold_long"

        # short
        self.best_price = min(self.best_price, close)
        reason = self._short_exit(close, prev_upper, trend_ma, atr)
        if reason:
            self._close()
            return BUY, reason
        return HOLD, "hold_short"

    def _long_exit(self, close, prev_lower, trend_ma, atr) -> str | None:
        cfg = self.cfg
        if close <= self.entry_price - cfg.atr_mult_stop * atr:
            return "long_exit_stop"
        if close <= self.best_price - cfg.atr_mult_exit * atr:
            return "long_exit_giveback"
        if close < trend_ma:
            return "long_exit_trend"
        if close < prev_lower:
            return "long_exit_breakout"
        return None

    def _short_exit(self, close, prev_upper, trend_ma, atr) -> str | None:
        cfg = self.cfg
        if close >= self.entry_price + cfg.atr_mult_stop * atr:
            return "short_exit_stop"
        if close >= self.best_price + cfg.atr_mult_exit * atr:
            return "short_exit_giveback"
        if close > trend_ma:
            return "short_exit_trend"
        if close > prev_upper:
            return "short_exit_breakout"
        return None

    def _open(self, direction: int, price: float) -> None:
        self.position = direction
        self.entry_price = price
        self.best_price = price
        self.bars_since_entry = 0

    def _close(self) -> None:
        self.position = 0
        self.entry_price = None
        self.best_price = None
        self.bars_since_entry = 0
        self.cooldown_remaining = self.cfg.cooldown_bars


class TrendBreakoutAtrStrategy:
    """Adapter: drive :class:`TrendBreakoutAtrEngine` from feature snapshots."""

    def __init__(self, config: TrendBreakoutAtrConfig) -> None:
        self._config = config
        self._engine = TrendBreakoutAtrEngine(config)
        self.last_reason = "warmup_hold"

    @property
    def position(self) -> int:
        return self._engine.position

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        close = snapshot.value(_CLOSE)
        high = snapshot.value(_HIGH)
        low = snapshot.value(_LOW)
        if close is None or high is None or low is None:
            self.last_reason = "warmup_hold"
            return HOLD
        signal, reason = self._engine.update(float(close), float(high), float(low))
        self.last_reason = reason
        return signal


PLUGIN = StrategyPlugin(
    name="trend_breakout_atr",
    config_cls=TrendBreakoutAtrConfig,
    strategy_cls=TrendBreakoutAtrStrategy,
    build_specs=build_specs,
    default_config_path="configs/backtests/trend_breakout_atr_btcusdt_1m_3d.yaml",
)
