"""ADX + MA-channel long — pure decision engine (position-aware).

Holds **only** the signal-decision maths (plain-Python; no ``strategy_framework``
/ ``nautilus_trader`` / ``pandas``). The low-level indicator primitives (EMA and
Wilder ADX) come from the shared ``feature_engine.indicators`` library rather than
being re-implemented inline. Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``ADXandMAChannelSys_L`` system (long mirror of
``ADXandMAChannelSys_S``):

* Wilder DMI/ADX with length ``DMI_N`` (14). Seeded at ``CurrentBar == DMI_N`` from
  a simple average of the first ``DMI_N`` +DM / -DM / TrueRange values, then
  Wilder-smoothed (``SF = 1/DMI_N``). Only ``ADXValue`` (= ``oADX``) and its prior
  value are read by the strategy; ``oADXR`` / ``DMI_M`` are computed in the source
  but never affect the entry or exit, so they are omitted here.
* ``UpperMA = XAverage(High, AvgLen)`` and ``LowerMA = XAverage(Low, AvgLen)`` — the
  EMA channel; ``ChanSpread = (UpperMA - LowerMA) / 2``.
* Buy-setup: ``Close > UpperMA And ADXValue > ADXValue[1]`` (price over the high
  EMA while ADX is rising). On a setup bar ``BuyTarget = Close + ChanSpread``;
  otherwise ``BuyTarget`` carries its previous value forward (TB ``Series``).
* ``MROBS = NthCon(BuySetup, 1)`` — bars ago (0 = current) of the most recent
  buy-setup; ``If MROBS > EntryBar Then MROBS = 0`` clamps a stale setup back to 0.
* Entry (long): ``MROBS[1] <> 0`` and flat and ``CurrentBar > 100`` and
  ``High >= BuyTarget[1]`` and ``Vol > 0`` -> long at ``Max(Open, BuyTarget[1])``.
* Exit (sell), once ``BarsSinceEntry > 0`` and ``Vol > 0``:
  ``Low <= UpperMA[1] - minpoint`` -> sell at ``Min(Open, UpperMA[1] - minpoint)``.

Faithful TradeBlazer semantics preserved: the **entry** reads the previous-bar
``MROBS[1]``, ``BuyTarget[1]`` and the **exit** reads ``UpperMA[1]`` (all
snapshotted before the end-of-bar roll); ``ADXValue > ADXValue[1]`` compares the
current ADX to its prior value; ``MarketPosition`` uses the bar-start position and
the exit is gated by ``BarsSinceEntry > 0`` so entry and sell never fire on one
bar. The ``Vol > 0`` gates are kept (synthetic bars carry volume 0, so real fills
need real bars). ``XAverage`` is the standard EMA (alpha = 2/(period+1)); the
``CurrentBar > 100`` warm-up gate is faithful to the source. NOTE the long uses
``UpperMA`` (high EMA) for both the setup and the exit, where the short used
``LowerMA``.
"""
from __future__ import annotations

from feature_engine.indicators import Ema, WilderADX

from strategies.adx_ma_channel_long.config import AdxMaChannelLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

# CurrentBar > 100 entry gate, hard-coded in the TradeBlazer source.
_WARMUP_BARS = 100


class AdxMaChannelLongEngine:
    """Pure, position-aware ADX + MA-channel long engine."""

    def __init__(self, config: AdxMaChannelLongConfig) -> None:
        self.cfg = config
        self._adx = WilderADX(config.dmi_n)
        self._ema_high = Ema(config.avg_len)
        self._ema_low = Ema(config.avg_len)
        self._current_bar = -1

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # bars ago of the most recent buy-setup (independent of the clamp).
        self._bars_since_setup = 10**9

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_adx: float | None = None
        self._prev_mrobs = 0              # MROBS series seeds at 0
        self._buy_target = 0.0            # BuyTarget series seeds at 0
        self._prev_upper_ma: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._current_bar += 1

        adx = self._adx.update(high, low, close)
        upper_ma = self._ema_high.update(high)
        lower_ma = self._ema_low.update(low)
        chan_spread = (upper_ma - lower_ma) * 0.5

        adx_rising = adx is not None and self._prev_adx is not None and adx > self._prev_adx
        buy_setup = close > upper_ma and adx_rising

        # BuyTarget: update on a setup bar, otherwise carry the previous value.
        prev_buy_target = self._buy_target
        buy_target = close + chan_spread if buy_setup else prev_buy_target

        # MROBS = NthCon(BuySetup, 1), then clamp a stale setup back to 0.
        self._bars_since_setup = 0 if buy_setup else self._bars_since_setup + 1
        mrobs = self._bars_since_setup if self._bars_since_setup <= cfg.entry_bar else 0

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # ENTRY (open long): a still-valid prior buy-setup and a break of the
        # previous target; MROBS[1] / BuyTarget[1] are the previous-bar values.
        if (
            not acted and mp_start == 0 and self._prev_mrobs != 0
            and self._current_bar > _WARMUP_BARS
            and high >= prev_buy_target and volume > 0
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = max(open_, prev_buy_target)
            signal, reason, acted = BUY, "enter_long", True

        # EXIT (sell): price breaks back below the previous high EMA - one tick.
        if not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0:
            if self._prev_upper_ma is not None and low <= self._prev_upper_ma - cfg.tick:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_ema_break", True

        # Roll the prev-bar snapshots, then advance counters.
        self._prev_adx = adx
        self._prev_mrobs = mrobs
        self._buy_target = buy_target
        self._prev_upper_ma = upper_ma
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
