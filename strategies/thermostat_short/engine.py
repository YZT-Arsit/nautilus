"""Thermostat short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Thermostat_S`` regime-switching system:

* **CMI** (Choppy Market Index) ``cmiVal = |Close - Close[29]| / (Highest(High,30)
  - Lowest(Low,30)) * 100``. ``cmiVal[1] < swing_trend_switch`` -> *swing* regime;
  otherwise *trend* regime.
* **swing regime** (opening-range ATR breakout): ``keyOfDay = (H+L+C)/3``; the
  prior close vs prior keyOfDay picks the "easier" side, sizing the ATR offsets
  (``swing_prcnt1`` near / ``swing_prcnt2`` far). ``swingSellPt`` is clamped by
  ``Min(., Average(High,3)[1])`` and ``swingBuyPt`` by ``Max(., Average(Low,3)[1])``.
  Enter short when ``Low <= swingSellPt``; cover when ``High >= swingBuyPt``.
* **trend regime** (Bollinger breakout): ``upBand/dnBand = SMA(Close,N) ±
  num_std_devs*StdDev(Close,N)``. A *swing*-entered position exits on the ATR
  protective stop ``High >= EntryPrice + 3*ATR[1]``. Otherwise enter short when
  ``Low <= dnBand[1]`` (needs ``BarsSinceExit >= 1``); cover when ``High >=
  Min(upBand[1], SMA(Close,trend_liq_length)[1])``.

Faithful TradeBlazer semantics preserved:

* ``MarketPosition`` is the **bar-start** position (``mp_start``): entries test
  ``mp_start != -1`` (flat) and exits ``mp_start == -1`` (short), so an entry and
  an exit never both fill on one bar. Every exit is additionally gated by
  ``BarsSinceEntry >= 1`` (an entry bar never exits).
* ``swingEntry`` flag distinguishes a swing-entered position (exits via the ATR
  protective stop in the trend regime) from a trend-entered one; it flips to
  ``False`` on every cover, mid-bar, exactly as the TradeBlazer series does.
* Trend thresholds read the **previous** bar's bands / MA / ATR (``[1]``); the
  swing trigger prices use the current Open with the previous bar's ATR / 3-bar
  ranges. There is **no** ``Vol > 0`` gate in ``Thermostat_S`` (unlike the VWM /
  breakout ports), so this engine does not gate on volume.

Fidelity notes:

* ``AvgTrueRange`` is approximated as a simple mean of true range over
  ``atr_length`` (matches ``trend_breakout_atr`` / ``trendscore_*``; the
  TradeBlazer builtin uses Wilder smoothing) — keeps the engine Nautilus-free.
* ``StandardDev`` uses the population form (divide by N); for N=50 the difference
  vs the sample form is ~1% and immaterial to the band breakout.
* TradeBlazer fills at ``Min(Open, level)`` (short entry / trend cover) or
  ``Max(Open, level)`` (covers); on the shared string-signal path the fill is a
  market fill at the signal bar (same accepted limitation as ``vwm_short``). The
  engine still books ``entry_price`` internally so the ATR stop stays faithful.
"""
from __future__ import annotations

import math
from collections import deque

from strategies.thermostat_short.config import ThermostatShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_BIG = 10**9  # bars_since_exit sentinel: entries allowed before the first exit


class ThermostatShortEngine:
    """Pure, position-aware Thermostat short engine."""

    def __init__(self, config: ThermostatShortConfig) -> None:
        self.cfg = config
        self._closes30: deque[float] = deque(maxlen=30)  # CMI: Close vs Close[29]
        self._highs30: deque[float] = deque(maxlen=30)   # CMI: Highest(High,30)
        self._lows30: deque[float] = deque(maxlen=30)    # CMI: Lowest(Low,30)
        self._lows3: deque[float] = deque(maxlen=3)      # trendLokBuy = Average(Low,3)
        self._highs3: deque[float] = deque(maxlen=3)     # trendLokSell = Average(High,3)
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._boll: deque[float] = deque(maxlen=config.bollinger_length)
        self._trend_closes: deque[float] = deque(maxlen=config.trend_liq_length)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.bars_since_exit = _BIG
        self.entry_price: float | None = None
        self.last_entry_price: float | None = None
        self.swing_entry = False          # True == current short came from the swing regime

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_cmi: float | None = None
        self._prev_close: float | None = None
        self._prev_key: float | None = None
        self._prev_atr: float | None = None
        self._prev_lok_buy: float | None = None
        self._prev_lok_sell: float | None = None
        self._prev_upband: float | None = None
        self._prev_dnband: float | None = None
        self._prev_trend_prot: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # === current-bar indicators (decisions still read the prev-bar snapshots) ===
        self._closes30.append(close)
        self._highs30.append(high)
        self._lows30.append(low)
        if len(self._closes30) == 30:
            rng = max(self._highs30) - min(self._lows30)
            cmi = abs(close - self._closes30[0]) / rng * 100.0 if rng > 0 else 0.0
        else:
            cmi = None

        self._lows3.append(low)
        self._highs3.append(high)
        lok_buy = sum(self._lows3) / len(self._lows3) if len(self._lows3) == 3 else None
        lok_sell = sum(self._highs3) / len(self._highs3) if len(self._highs3) == 3 else None

        key = (high + low + close) / 3.0

        tr = high - low if self._tr_prev_close is None else max(
            high - low, abs(high - self._tr_prev_close), abs(low - self._tr_prev_close)
        )
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None

        self._boll.append(close)
        if len(self._boll) == cfg.bollinger_length:
            mid = sum(self._boll) / len(self._boll)
            var = sum((x - mid) ** 2 for x in self._boll) / len(self._boll)
            band = math.sqrt(var)
            upband = mid + cfg.num_std_devs * band
            dnband = mid - cfg.num_std_devs * band
        else:
            upband = dnband = None

        self._trend_closes.append(close)
        trend_prot = (sum(self._trend_closes) / len(self._trend_closes)
                      if len(self._trend_closes) == cfg.trend_liq_length else None)

        # swing trigger prices: current Open, previous-bar ATR / keyOfDay / 3-bar ranges.
        swing_buy_pt = swing_sell_pt = None
        if (self._prev_close is not None and self._prev_key is not None and self._prev_atr is not None
                and self._prev_lok_buy is not None and self._prev_lok_sell is not None):
            sell_easier = self._prev_close > self._prev_key   # else buy_easier
            near, far = cfg.swing_prcnt1, cfg.swing_prcnt2
            if sell_easier:
                swing_buy_pt = open_ + far * self._prev_atr
                swing_sell_pt = open_ - near * self._prev_atr
            else:
                swing_buy_pt = open_ + near * self._prev_atr
                swing_sell_pt = open_ - far * self._prev_atr
            swing_buy_pt = max(swing_buy_pt, self._prev_lok_buy)
            swing_sell_pt = min(swing_sell_pt, self._prev_lok_sell)

        # === decisions (mp_start == TradeBlazer bar-start MarketPosition) ===
        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        swing_regime = self._prev_cmi is not None and self._prev_cmi < cfg.swing_trend_switch
        trend_regime = self._prev_cmi is not None and self._prev_cmi >= cfg.swing_trend_switch

        if swing_regime:
            # swing entry (opening-range ATR breakout)
            if not acted and mp_start != -1 and swing_sell_pt is not None and low <= swing_sell_pt:
                self._enter_short(min(open_, swing_sell_pt))
                self.swing_entry = True
                signal, reason, acted = SELL, "swing_enter_short", True
            # swing cover
            if (not acted and mp_start == -1 and self.bars_since_entry >= 1
                    and swing_buy_pt is not None and high >= swing_buy_pt):
                self._cover()
                self.swing_entry = False
                signal, reason, acted = BUY, "swing_cover", True

        if trend_regime:
            # a swing-entered position exits on the ATR protective stop
            if self.swing_entry:
                if (not acted and mp_start == -1 and self.bars_since_entry >= 1
                        and self.entry_price is not None and self._prev_atr is not None):
                    stop = self.entry_price + 3.0 * self._prev_atr
                    if high >= stop:
                        self._cover()
                        self.swing_entry = False
                        signal, reason, acted = BUY, "swing_prot_stop_cover", True
            # trend-regime entries / exits (swing_entry may have just flipped to False)
            if not self.swing_entry:
                if (not acted and mp_start != -1 and self.bars_since_exit >= 1
                        and self._prev_dnband is not None and low <= self._prev_dnband):
                    self._enter_short(min(open_, self._prev_dnband))
                    # trend entry keeps swing_entry == False
                    signal, reason, acted = SELL, "trend_enter_short", True
                if (not acted and mp_start == -1 and self.bars_since_entry >= 1
                        and self._prev_upband is not None and self._prev_trend_prot is not None):
                    line = min(self._prev_upband, self._prev_trend_prot)
                    if high >= line:
                        self._cover()
                        signal, reason, acted = BUY, "trend_cover", True

        # === roll the prev-bar snapshots, then advance counters ===
        self._prev_cmi = cmi
        self._prev_close = close
        self._prev_key = key
        self._prev_atr = atr
        self._prev_lok_buy = lok_buy
        self._prev_lok_sell = lok_sell
        self._prev_upband = upband
        self._prev_dnband = dnband
        self._prev_trend_prot = trend_prot
        self.bars_since_exit += 1
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _enter_short(self, price: float) -> None:
        self.position = -1
        self.bars_since_entry = 0
        self.entry_price = price
        self.last_entry_price = price

    def _cover(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.bars_since_exit = 0
        self.entry_price = None
        self.last_entry_price = None
