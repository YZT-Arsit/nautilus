"""ADX + MA-channel short — pure decision engine (position-aware).

Holds **only** the signal-decision maths (plain-Python; no ``strategy_framework`` /
``nautilus_trader`` / ``pandas``). The low-level indicator primitives (EMA and
Wilder ADX) come from the shared ``feature_engine.indicators`` library rather than
being re-implemented inline. Emits ``BUY``/``SELL``/``HOLD`` with the signal->order
meaning left to ``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the
short, BUY covers it). Single unit, no pyramiding.

Ported from the TradeBlazer ``ADXandMAChannelSys_S`` system:

* Wilder DMI/ADX (``feature_engine.indicators.WilderADX``) with length ``DMI_N``
  (14). Seeded at ``CurrentBar == DMI_N`` from
  a simple average of the first ``DMI_N`` +DM / -DM / TrueRange values, then
  Wilder-smoothed (``SF = 1/DMI_N``). Only ``ADXValue`` (= ``oADX``) and its prior
  value are read by the strategy; ``oADXR`` / ``DMI_M`` are computed in the source
  but never affect the entry or exit, so they are omitted here.
* ``UpperMA = XAverage(High, AvgLen)`` and ``LowerMA = XAverage(Low, AvgLen)`` — the
  EMA channel; ``ChanSpread = (UpperMA - LowerMA) / 2``.
* Sell-setup: ``Close < LowerMA And ADXValue > ADXValue[1]`` (price under the low
  EMA while ADX is rising). On a setup bar ``SellTarget = Close - ChanSpread``;
  otherwise ``SellTarget`` carries its previous value forward (TB ``Series``).
* ``MROSS = NthCon(SellSetup, 1)`` — bars ago (0 = current) of the most recent
  sell-setup; ``If MROSS > EntryBar Then MROSS = 0`` clamps a stale setup back to 0.
* Entry (short): ``MROSS[1] <> 0`` and flat and ``CurrentBar > 100`` and
  ``Low <= SellTarget[1]`` and ``Vol > 0`` -> short at ``Min(Open, SellTarget[1])``.
* Exit (cover), once ``BarsSinceEntry > 0`` and ``Vol > 0``:
  ``High >= LowerMA[1] + minpoint`` -> cover at ``Max(Open, LowerMA[1] + minpoint)``.

Faithful TradeBlazer semantics preserved: the **entry** reads the previous-bar
``MROSS[1]``, ``SellTarget[1]`` and the **exit** reads ``LowerMA[1]`` (all
snapshotted before the end-of-bar roll); ``ADXValue > ADXValue[1]`` compares the
current ADX to its prior value; ``MarketPosition`` uses the bar-start position and
the exit is gated by ``BarsSinceEntry > 0`` so entry and cover never fire on one
bar. The ``Vol > 0`` gates are kept (synthetic bars carry volume 0, so real fills
need real bars). ``XAverage`` is the standard EMA (alpha = 2/(period+1)); the
``CurrentBar > 100`` warm-up gate is faithful to the source.
"""
from __future__ import annotations

from feature_engine.indicators import Ema, WilderADX

from strategies.adx_ma_channel_short.config import AdxMaChannelShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

# CurrentBar > 100 entry gate, hard-coded in the TradeBlazer source.
_WARMUP_BARS = 100


class AdxMaChannelShortEngine:
    """Pure, position-aware ADX + MA-channel short engine."""

    def __init__(self, config: AdxMaChannelShortConfig) -> None:
        self.cfg = config
        self._adx = WilderADX(config.dmi_n)
        self._ema_high = Ema(config.avg_len)
        self._ema_low = Ema(config.avg_len)
        self._current_bar = -1

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # bars ago of the most recent sell-setup (independent of the clamp).
        self._bars_since_setup = 10**9

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_adx: float | None = None
        self._prev_mross = 0              # MROSS series seeds at 0
        self._sell_target = 0.0           # SellTarget series seeds at 0
        self._prev_lower_ma: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._current_bar += 1

        adx = self._adx.update(high, low, close)
        upper_ma = self._ema_high.update(high)
        lower_ma = self._ema_low.update(low)
        chan_spread = (upper_ma - lower_ma) * 0.5

        adx_rising = adx is not None and self._prev_adx is not None and adx > self._prev_adx
        sell_setup = close < lower_ma and adx_rising

        # SellTarget: update on a setup bar, otherwise carry the previous value.
        prev_sell_target = self._sell_target
        sell_target = close - chan_spread if sell_setup else prev_sell_target

        # MROSS = NthCon(SellSetup, 1), then clamp a stale setup back to 0.
        self._bars_since_setup = 0 if sell_setup else self._bars_since_setup + 1
        mross = self._bars_since_setup if self._bars_since_setup <= cfg.entry_bar else 0

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # ENTRY (open short): a still-valid prior sell-setup and a break of the
        # previous target; MROSS[1] / SellTarget[1] are the previous-bar values.
        if (
            not acted and mp_start == 0 and self._prev_mross != 0
            and self._current_bar > _WARMUP_BARS
            and low <= prev_sell_target and volume > 0
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = min(open_, prev_sell_target)
            signal, reason, acted = SELL, "enter_short", True

        # EXIT (cover): price breaks back above the previous low EMA + one tick.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0:
            if self._prev_lower_ma is not None and high >= self._prev_lower_ma + cfg.tick:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_ema_break", True

        # Roll the prev-bar snapshots, then advance counters.
        self._prev_adx = adx
        self._prev_mross = mross
        self._sell_target = sell_target
        self._prev_lower_ma = lower_ma
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
