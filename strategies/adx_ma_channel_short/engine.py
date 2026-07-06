"""ADX + MA-channel short — pure decision engine (position-aware).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``ADXandMAChannelSys_S`` system:

* Wilder DMI/ADX with length ``DMI_N`` (14). Seeded at ``CurrentBar == DMI_N`` from
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

from collections import deque

from strategies.adx_ma_channel_short.config import AdxMaChannelShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

# CurrentBar > 100 entry gate, hard-coded in the TradeBlazer source.
_WARMUP_BARS = 100


class _Ema:
    """Standard EMA (XAverage): seed with the first value, alpha = 2/(period+1)."""

    def __init__(self, period: int) -> None:
        self._alpha = 2.0 / (period + 1.0)
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.value = x
        else:
            self.value += self._alpha * (x - self.value)
        return self.value


class _WilderADX:
    """Wilder DMI/ADX, matching the ADXandMAChannelSys DMI block.

    Returns the current ``oADX`` (``sADX``) value, or ``None`` until it is defined
    (``CurrentBar >= 1``). ``oADXR`` / ``DMI_M`` are not needed by the strategy and
    are not computed.
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self.sf = 1.0 / n
        self.current_bar = -1
        self.avg_plus: float | None = None
        self.avg_minus: float | None = None
        self.svolty: float | None = None
        self.sadx: float | None = None
        self.cumm = 0.0
        self._high_hist: deque[float] = deque(maxlen=n + 1)
        self._low_hist: deque[float] = deque(maxlen=n + 1)
        self._tr_hist: deque[float] = deque(maxlen=n)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        self.current_bar += 1
        cb = self.current_bar

        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._high_hist.append(high)
        self._low_hist.append(low)
        self._tr_hist.append(tr)

        if cb == self.n:
            sum_plus = sum_minus = sum_tr = 0.0
            for i in range(self.n):
                hi, hi1 = self._high_hist[-1 - i], self._high_hist[-2 - i]
                lo, lo1 = self._low_hist[-1 - i], self._low_hist[-2 - i]
                upper, lower = hi - hi1, lo1 - lo
                if upper > lower and upper > 0:
                    sum_plus += upper
                elif lower > upper and lower > 0:
                    sum_minus += lower
                sum_tr += self._tr_hist[-1 - i]
            self.avg_plus = sum_plus / self.n
            self.avg_minus = sum_minus / self.n
            self.svolty = sum_tr / self.n
        elif cb > self.n:
            upper = high - self._prev_high
            lower = self._prev_low - low
            plus = minus = 0.0
            if upper > lower and upper > 0:
                plus = upper
            elif lower > upper and lower > 0:
                minus = lower
            self.avg_plus += self.sf * (plus - self.avg_plus)
            self.avg_minus += self.sf * (minus - self.avg_minus)
            self.svolty += self.sf * (tr - self.svolty)

        if self.svolty is not None and self.svolty > 0:
            odmi_plus = 100.0 * self.avg_plus / self.svolty
            odmi_minus = 100.0 * self.avg_minus / self.svolty
        else:
            odmi_plus = odmi_minus = 0.0
        divisor = odmi_plus + odmi_minus
        sdmi = 100.0 * abs(odmi_plus - odmi_minus) / divisor if divisor > 0 else 0.0
        self.cumm += sdmi

        adx: float | None = None
        if cb > 0:
            if cb <= self.n:
                self.sadx = self.cumm / cb
            else:
                self.sadx += self.sf * (sdmi - self.sadx)
            adx = self.sadx

        self._prev_high, self._prev_low, self._prev_close = high, low, close
        return adx


class AdxMaChannelShortEngine:
    """Pure, position-aware ADX + MA-channel short engine."""

    def __init__(self, config: AdxMaChannelShortConfig) -> None:
        self.cfg = config
        self._adx = _WilderADX(config.dmi_n)
        self._ema_high = _Ema(config.avg_len)
        self._ema_low = _Ema(config.avg_len)
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
