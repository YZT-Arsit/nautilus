"""Thermostat long — pure decision engine (position-aware, offline-testable).

Long-side mirror of ``strategies/thermostat_short/engine.py``. Holds **only** the
signal-decision maths (plain-Python; no ``feature_engine`` / ``strategy_framework``
/ ``nautilus_trader`` / ``pandas``). Emits ``BUY``/``SELL``/``HOLD`` with the
signal->order meaning left to ``SignalToOrderPolicy`` (``sell_means: flat`` — BUY
opens the long, SELL flattens it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Thermostat_L`` regime-switching system:

* **CMI** (Choppy Market Index) ``cmiVal = |Close - Close[29]| / (Highest(High,30)
  - Lowest(Low,30)) * 100``. ``cmiVal[1] < swing_trend_switch`` -> *swing* regime;
  otherwise *trend* regime.
* **swing regime** (opening-range ATR breakout): ``keyOfDay = (H+L+C)/3``; the
  prior close vs prior keyOfDay picks the "easier" side, sizing the ATR offsets
  (``swing_prcnt1`` near / ``swing_prcnt2`` far). ``swingBuyPt`` is clamped by
  ``Max(., Average(Low,3)[1])`` and ``swingSellPt`` by ``Min(., Average(High,3)[1])``.
  Enter long when ``High >= swingBuyPt``; exit when ``Low <= swingSellPt``.
* **trend regime** (Bollinger breakout): ``upBand/dnBand = SMA(Close,N) ±
  num_std_devs*StdDev(Close,N)``. A *swing*-entered position exits on the ATR
  protective stop ``Low <= EntryPrice - 3*ATR[1]``. Otherwise enter long when
  ``High >= upBand[1]`` (needs ``BarsSinceExit >= 1``); exit when ``Low <=
  Max(dnBand[1], SMA(Close,trend_liq_length)[1])``.

Faithful TradeBlazer semantics preserved (identical to the short engine, mirrored
to the long side):

* ``MarketPosition`` is the **bar-start** position (``mp_start``): entries test
  ``mp_start != 1`` (flat) and exits ``mp_start == 1`` (long), so an entry and an
  exit never both fill on one bar. Every exit is additionally gated by
  ``BarsSinceEntry >= 1`` (an entry bar never exits).
* ``swingEntry`` flag distinguishes a swing-entered position (exits via the ATR
  protective stop in the trend regime) from a trend-entered one; it flips to
  ``False`` on every exit, mid-bar, exactly as the TradeBlazer series does.
* Trend thresholds read the **previous** bar's bands / MA / ATR (``[1]``); the
  swing trigger prices use the current Open with the previous bar's ATR / 3-bar
  ranges. There is **no** ``Vol > 0`` gate in ``Thermostat_L``, so this engine
  does not gate on volume.

Fidelity notes (same as the short engine): simple-mean ATR over ``atr_length``
(TradeBlazer uses Wilder smoothing); population ``StandardDev`` (≈1% off the
sample form at N=50, immaterial); ``Max(Open, level)`` / ``Min(Open, level)``
fills modelled as a market fill at the signal bar on the shared string path.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import rolling_std, simple_atr, sma, true_range
from strategies.thermostat_long.config import ThermostatLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_BIG = 10**9  # bars_since_exit sentinel: entries allowed before the first exit


class ThermostatLongEngine:
    """Pure, position-aware Thermostat long engine."""

    def __init__(self, config: ThermostatLongConfig) -> None:
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
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.bars_since_exit = _BIG
        self.entry_price: float | None = None
        self.last_entry_price: float | None = None
        self.swing_entry = False          # True == current long came from the swing regime

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
        lok_buy = sma(self._lows3) if len(self._lows3) == 3 else None
        lok_sell = sma(self._highs3) if len(self._highs3) == 3 else None

        key = (high + low + close) / 3.0

        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_length)

        self._boll.append(close)
        if len(self._boll) == cfg.bollinger_length:
            mid = sma(self._boll)
            band = rolling_std(self._boll, ddof=0)
            upband = mid + cfg.num_std_devs * band
            dnband = mid - cfg.num_std_devs * band
        else:
            upband = dnband = None

        self._trend_closes.append(close)
        trend_prot = (sma(self._trend_closes)
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
            # swing entry (opening-range ATR breakout up)
            if not acted and mp_start != 1 and swing_buy_pt is not None and high >= swing_buy_pt:
                self._enter_long(max(open_, swing_buy_pt))
                self.swing_entry = True
                signal, reason, acted = BUY, "swing_enter_long", True
            # swing exit
            if (not acted and mp_start == 1 and self.bars_since_entry >= 1
                    and swing_sell_pt is not None and low <= swing_sell_pt):
                self._flat()
                self.swing_entry = False
                signal, reason, acted = SELL, "swing_exit", True

        if trend_regime:
            # a swing-entered position exits on the ATR protective stop
            if self.swing_entry:
                if (not acted and mp_start == 1 and self.bars_since_entry >= 1
                        and self.entry_price is not None and self._prev_atr is not None):
                    stop = self.entry_price - 3.0 * self._prev_atr
                    if low <= stop:
                        self._flat()
                        self.swing_entry = False
                        signal, reason, acted = SELL, "swing_prot_stop_exit", True
            # trend-regime entries / exits (swing_entry may have just flipped to False)
            if not self.swing_entry:
                if (not acted and mp_start != 1 and self.bars_since_exit >= 1
                        and self._prev_upband is not None and high >= self._prev_upband):
                    self._enter_long(max(open_, self._prev_upband))
                    # trend entry keeps swing_entry == False
                    signal, reason, acted = BUY, "trend_enter_long", True
                if (not acted and mp_start == 1 and self.bars_since_entry >= 1
                        and self._prev_dnband is not None and self._prev_trend_prot is not None):
                    line = max(self._prev_dnband, self._prev_trend_prot)
                    if low <= line:
                        self._flat()
                        signal, reason, acted = SELL, "trend_exit", True

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
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _enter_long(self, price: float) -> None:
        self.position = 1
        self.bars_since_entry = 0
        self.entry_price = price
        self.last_entry_price = price

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.bars_since_exit = 0
        self.entry_price = None
        self.last_entry_price = None
