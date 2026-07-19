"""First-PullBack short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``strategy_framework``
/ ``nautilus_trader`` / ``pandas``). The low-level indicator primitives (EMA and
true range) come from the shared ``feature_engine.indicators`` library rather than
being re-implemented inline. Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``FirstPullBackSys_S`` system:

* ``MACDLine = XAverage(Close, FastMA) - XAverage(Close, SlowMA)``,
  ``SignalLine = XAverage(MACDLine, AvgMA)``, ``AATR = AvgTrueRange(ATRLen)``;
* ``Con1 = CrossOver(SignalLine, 0)`` -> uptrend (UpTrend=True, DnTrend/SellSetup/
  SignalFlag=False); ``Con2 = CrossUnder(SignalLine, 0)`` -> downtrend
  (DnTrend=True, UpTrend/SignalFlag=False);
* while ``DnTrend``: if ``SignalFlag == False`` set ``SellSetup=True`` and record
  ``CTrendHigh=High``; then if ``MACDLine > SignalLine And High > CTrendHigh[1]``
  push ``CTrendHigh`` up to the new high;
* the bar SellSetup first turns true (``SellSetup[1] And not SellSetup[2]``) arms
  the channel ``Lowerband = Close[1] - EATRPcnt*AATR[1]`` and ``Exitband =
  Close[1] + XATRPcnt*AATR[1]`` (both held for the rest of the downtrend);
* entry (short): ``SellSetup[1]`` and flat and ``Low <= Lowerband`` -> short at
  ``Min(Open, Lowerband)`` (then ``SellSetup=False``, ``SignalFlag=True`` so the
  trend arms only once);
* exit (cover), once ``BarsSinceEntry > 0``: ``UpTrend[1]`` -> cover at Open
  (``exit_uptrend``); else ``High >= CTrendHigh[1]+tick And CTrendHigh[1]+tick <=
  Exitband`` -> cover at ``Max(Open, CTrendHigh[1]+tick)`` (``exit_trend_high``);
  else ``High >= Exitband`` -> cover at ``Max(Open, Exitband)`` (``exit_band``).

Faithful TradeBlazer semantics preserved: ``XAverage`` is a standard EMA seeded
with the first value (``alpha = 2/(N+1)``); the zero-crosses, the channel arm, the
entry gate (``SellSetup[1]``) and every exit read the **previous-bar** value
(``SignalLine[1]``, ``SellSetup[1]``/``[2]``, ``UpTrend[1]``, ``CTrendHigh[1]``,
``Close[1]``, ``AATR[1]``) snapshotted before the roll; ``MarketPosition`` uses the
bar-start position and exits are gated by ``BarsSinceEntry > 0`` so entry and cover
never fire on one bar. There is **no** ``Vol > 0`` gate (matches the source). ATR is
a simple mean of true range (documented deviation from any Wilder builtin).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import Ema, simple_atr, true_range

from strategies.first_pullback_short.config import FirstPullbackShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class FirstPullbackShortEngine:
    """Pure, position-aware First-PullBack short engine."""

    def __init__(self, config: FirstPullbackShortConfig) -> None:
        self.cfg = config
        self._ema_fast = Ema(config.fast_ma)
        self._ema_slow = Ema(config.slow_ma)
        self._ema_signal = Ema(config.avg_ma)
        self._trs: deque[float] = deque(maxlen=config.atr_len)
        self._tr_prev_close: float | None = None

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent trend / setup state (carry-forward until reassigned)
        self.uptrend = False
        self.dntrend = False
        self.sellsetup = False
        self.signalflag = False
        self.ctrend_high = 0.0
        self.lowerband: float | None = None
        self.exitband: float | None = None

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_signal: float | None = None
        self._prev_uptrend = False
        self._prev_sellsetup = False
        self._prev2_sellsetup = False
        self._prev_ctrend_high = 0.0
        self._prev_aatr: float | None = None
        self._prev_close: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg

        # 1. MACD line + signal line.
        macd = self._ema_fast.update(close) - self._ema_slow.update(close)
        signal_line = self._ema_signal.update(macd)

        # 2. ATR (simple mean of true range).
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_len)

        # 3. Zero-line crosses of the signal line.
        con1 = self._prev_signal is not None and self._prev_signal <= 0 and signal_line > 0
        con2 = self._prev_signal is not None and self._prev_signal >= 0 and signal_line < 0

        # 4. Trend state machine.
        if con1:
            self.uptrend = True
            self.dntrend = False
            self.signalflag = False
            self.sellsetup = False
        if con2:
            self.dntrend = True
            self.signalflag = False
            self.uptrend = False

        if self.dntrend:
            if not self.signalflag:
                self.sellsetup = True
                self.ctrend_high = high
            if macd > signal_line and high > self._prev_ctrend_high:
                self.ctrend_high = high

        # 5. Arm the entry / exit channel on the bar SellSetup first turned true.
        if self._prev_sellsetup and not self._prev2_sellsetup:
            if self._prev_close is not None and self._prev_aatr is not None:
                self.lowerband = self._prev_close - cfg.entry_atr_pcnt * self._prev_aatr
                self.exitband = self._prev_close + cfg.exit_atr_pcnt * self._prev_aatr

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 6. ENTRY (open short): setup armed last bar + break of the lower band.
        if (
            not acted and mp_start == 0 and self._prev_sellsetup
            and self.lowerband is not None and low <= self.lowerband
        ):
            entry_price = min(open_, self.lowerband)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.sellsetup = False
            self.signalflag = True
            signal, reason, acted = SELL, "enter_short", True

        # 7. EXIT (cover): trend-over, then trend-high band, then exit band.
        if not acted and mp_start == -1 and self.bars_since_entry > 0:
            trend_high_line = self._prev_ctrend_high + cfg.tick
            if self._prev_uptrend:
                self._cover()
                signal, reason, acted = BUY, "exit_uptrend", True
            elif (
                self.exitband is not None and high >= trend_high_line
                and trend_high_line <= self.exitband
            ):
                self._cover()
                signal, reason, acted = BUY, "exit_trend_high", True
            elif self.exitband is not None and high >= self.exitband:
                self._cover()
                signal, reason, acted = BUY, "exit_band", True

        # 8. Roll the prev-bar snapshots, then advance counters.
        self._prev_signal = signal_line
        self._prev_uptrend = self.uptrend
        self._prev2_sellsetup = self._prev_sellsetup
        self._prev_sellsetup = self.sellsetup
        self._prev_ctrend_high = self.ctrend_high
        self._prev_aatr = atr
        self._prev_close = close
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _cover(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
