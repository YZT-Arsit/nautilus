"""Dynamic Breakout II short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``DynamicBreakOutII_S`` system:

* an adaptive lookback ``lookBackDays`` starts at 20 and each bar is multiplied by
  ``1 + deltaVolatility`` where ``deltaVolatility = (todayVol - todayVol[1]) /
  todayVol`` and ``todayVol = StandardDev(Close, 30)``; the result is rounded and
  clamped to ``[floor_amt, ceiling_amt]`` and carried forward;
* over that adaptive window: ``MidLine = Average(Close, look)``, ``Band =
  StandardDev(Close, look)``, ``upBand/dnBand = MidLine ± bolBandTrig*Band``,
  Donchian ``buyPoint = Highest(High, look)`` / ``sellPoint = Lowest(Low, look)``,
  exit MA ``LiqPoint = MidLine``;
* entry (short): flat, ``Close[1] < dnBand[1]`` and ``Low <= sellPoint[1]`` ->
  short at ``Min(Open, sellPoint[1])``;
* cover (reverse): short and ``Close[1] > upBand[1]`` and ``High >= buyPoint[1]``
  -> cover at ``Max(Open, buyPoint[1])``;
* cover (liq), once ``BarsSinceEntry >= 1``: ``High >= LiqPoint[1]`` -> cover at
  ``Max(Open, LiqPoint[1])``.

Faithful TradeBlazer semantics preserved: every band / channel / MA level is read
at its **previous-bar** value (``upBand[1]``, ``dnBand[1]``, ``buyPoint[1]``,
``sellPoint[1]``, ``LiqPoint[1]``, ``Close[1]``) snapshotted before the roll, while
the trigger uses the current bar's ``High``/``Low``/``Open``; ``lookBackDays`` is a
carried-forward scalar (this bar's window depends on all prior windows);
``MarketPosition`` uses the bar-start position; the reverse cover has no
``BarsSinceEntry`` gate but the liq cover requires ``BarsSinceEntry >= 1``. Order
priority per bar: reverse cover, then (if flat) entry, then liq cover. There is
**no** ``Vol > 0`` gate (matches the source).

Convention notes (documented deviations): ``StandardDev`` for ``todayVol`` uses the
population divisor (``ddof=0``, TB DataType 1) and ``Band`` the sample divisor
(``ddof=1``, TB DataType 2); ``deltaVolatility`` is a ratio so the ``todayVol``
divisor cancels. ``Round`` uses round-half-up. ``Average`` is a simple mean.
"""
from __future__ import annotations

import math
from collections import deque

from strategies.dynamic_breakout_short.config import DynamicBreakoutShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_VOL_LEN = 30            # StandardDev(Close, 30) window (TB hard-codes 30).
_INITIAL_LOOKBACK = 20   # lookBackDays starts at 20 (TB init).


def _std(vals: list[float], ddof: int) -> float:
    n = len(vals)
    if n - ddof <= 0:
        return 0.0
    mean = sum(vals) / n
    var = sum((x - mean) ** 2 for x in vals) / (n - ddof)
    return math.sqrt(var)


class DynamicBreakoutShortEngine:
    """Pure, position-aware Dynamic Breakout II short engine."""

    def __init__(self, config: DynamicBreakoutShortConfig) -> None:
        self.cfg = config
        maxlen = max(_VOL_LEN, config.ceiling_amt)
        self._closes: deque[float] = deque(maxlen=maxlen)
        self._highs: deque[float] = deque(maxlen=maxlen)
        self._lows: deque[float] = deque(maxlen=maxlen)

        self.look_back_days = float(_INITIAL_LOOKBACK)

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_today_vol: float | None = None
        self._prev_close: float | None = None
        self._prev_upband: float | None = None
        self._prev_dnband: float | None = None
        self._prev_buypoint: float | None = None
        self._prev_sellpoint: float | None = None
        self._prev_liqpoint: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._closes.append(close)
        self._highs.append(high)
        self._lows.append(low)

        # 1. 30-bar volatility and its change -> adaptive lookback (carried forward).
        today_vol = (
            _std(list(self._closes)[-_VOL_LEN:], ddof=0)
            if len(self._closes) >= _VOL_LEN else None
        )
        yester_vol = self._prev_today_vol
        if today_vol is None or yester_vol is None or today_vol == 0:
            delta = 0.0
        else:
            delta = (today_vol - yester_vol) / today_vol
        look = self.look_back_days * (1.0 + delta)
        look = math.floor(look + 0.5)                 # Round(x, 0), half-up
        look = min(look, cfg.ceiling_amt)
        look = max(look, cfg.floor_amt)
        self.look_back_days = float(look)

        # 2. Adaptive Bollinger / Donchian / exit-MA levels (current bar inclusive).
        if len(self._closes) >= look:
            cl = list(self._closes)[-look:]
            hi = list(self._highs)[-look:]
            lo = list(self._lows)[-look:]
            midline = sum(cl) / look
            band = _std(cl, ddof=1)
            upband = midline + cfg.bol_band_trig * band
            dnband = midline - cfg.bol_band_trig * band
            buypoint = max(hi)
            sellpoint = min(lo)
            liqpoint = midline
        else:
            midline = band = upband = dnband = buypoint = sellpoint = liqpoint = None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        pc = self._prev_close

        # 3. COVER (reverse): short + prior close above upper band + upper breakout.
        if (
            not acted and mp_start == -1 and pc is not None
            and self._prev_upband is not None and self._prev_buypoint is not None
            and pc > self._prev_upband and high >= self._prev_buypoint
        ):
            self._cover()
            signal, reason, acted = BUY, "exit_reverse", True

        # 4. ENTRY (open short): flat + prior close below lower band + lower breakout.
        if (
            not acted and mp_start != -1 and pc is not None
            and self._prev_dnband is not None and self._prev_sellpoint is not None
            and pc < self._prev_dnband and low <= self._prev_sellpoint
        ):
            entry_price = min(open_, self._prev_sellpoint)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 5. COVER (liq): short a full bar + cross back above the exit MA.
        if (
            not acted and mp_start == -1 and self.bars_since_entry >= 1
            and self._prev_liqpoint is not None and high >= self._prev_liqpoint
        ):
            self._cover()
            signal, reason, acted = BUY, "exit_liq", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._prev_today_vol = today_vol
        self._prev_close = close
        self._prev_upband = upband
        self._prev_dnband = dnband
        self._prev_buypoint = buypoint
        self._prev_sellpoint = sellpoint
        self._prev_liqpoint = liqpoint
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _cover(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
