"""First-PullBack long — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``FirstPullBackSys_L`` system (long mirror of
``FirstPullBackSys_S``):

* ``MACDLine = XAverage(Close, FastMA) - XAverage(Close, SlowMA)``,
  ``SignalLine = XAverage(MACDLine, AvgMA)``, ``AATR = AvgTrueRange(ATRLen)``;
* ``Con1 = CrossOver(SignalLine, 0)`` -> uptrend (UpTrend=True, DnTrend/
  SignalFlag=False); ``Con2 = CrossUnder(SignalLine, 0)`` -> downtrend
  (DnTrend=True, UpTrend/BuySetup/SignalFlag=False);
* while ``UpTrend``: if ``SignalFlag == False`` set ``BuySetup=True`` and record
  ``CTrendLow=Low``; then if ``MACDLine < SignalLine And Low < CTrendLow[1]`` push
  ``CTrendLow`` down to the new low;
* the bar BuySetup first turns true (``BuySetup[1] And not BuySetup[2]``) arms the
  channel ``Upperband = Close[1] + EATRPcnt*AATR[1]`` and ``Exitband = Close[1] -
  XATRPcnt*AATR[1]`` (both held for the rest of the uptrend);
* entry (long): ``BuySetup[1]`` and flat and ``High >= Upperband`` -> long at
  ``Max(Open, Upperband)`` (then ``BuySetup=False``, ``SignalFlag=True`` so the
  trend arms only once);
* exit (sell), once ``BarsSinceEntry > 0``: ``DnTrend[1]`` -> sell at Open
  (``exit_downtrend``); else ``Low <= CTrendLow[1]-tick And CTrendLow[1]-tick >=
  Exitband`` -> sell at ``Min(Open, CTrendLow[1]-tick)`` (``exit_trend_low``); else
  ``Low <= Exitband`` -> sell at ``Min(Open, Exitband)`` (``exit_band``).

Faithful TradeBlazer semantics preserved: ``XAverage`` is a standard EMA seeded
with the first value (``alpha = 2/(N+1)``); the zero-crosses, the channel arm, the
entry gate (``BuySetup[1]``) and every exit read the **previous-bar** value
(``SignalLine[1]``, ``BuySetup[1]``/``[2]``, ``DnTrend[1]``, ``CTrendLow[1]``,
``Close[1]``, ``AATR[1]``) snapshotted before the roll; ``MarketPosition`` uses the
bar-start position and exits are gated by ``BarsSinceEntry > 0`` so entry and sell
never fire on one bar. There is **no** ``Vol > 0`` gate (matches the source). ATR is
a simple mean of true range (documented deviation from any Wilder builtin).
"""
from __future__ import annotations

from collections import deque

from strategies.first_pullback_long.config import FirstPullbackLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class _Ema:
    """Standard EMA (XAverage): seed with the first value, alpha = 2/(period+1)."""

    def __init__(self, period: int) -> None:
        self._alpha = 2.0 / (period + 1.0)
        self.value: float | None = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            self.value += self._alpha * (x - self.value)
        return self.value


class FirstPullbackLongEngine:
    """Pure, position-aware First-PullBack long engine."""

    def __init__(self, config: FirstPullbackLongConfig) -> None:
        self.cfg = config
        self._ema_fast = _Ema(config.fast_ma)
        self._ema_slow = _Ema(config.slow_ma)
        self._ema_signal = _Ema(config.avg_ma)
        self._trs: deque[float] = deque(maxlen=config.atr_len)
        self._tr_prev_close: float | None = None

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent trend / setup state (carry-forward until reassigned)
        self.uptrend = False
        self.dntrend = False
        self.buysetup = False
        self.signalflag = False
        self.ctrend_low = 0.0
        self.upperband: float | None = None
        self.exitband: float | None = None

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_signal: float | None = None
        self._prev_dntrend = False
        self._prev_buysetup = False
        self._prev2_buysetup = False
        self._prev_ctrend_low = 0.0
        self._prev_aatr: float | None = None
        self._prev_close: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg

        # 1. MACD line + signal line.
        macd = self._ema_fast.update(close) - self._ema_slow.update(close)
        signal_line = self._ema_signal.update(macd)

        # 2. ATR (simple mean of true range).
        tr = high - low if self._tr_prev_close is None else max(
            high - low, abs(high - self._tr_prev_close), abs(low - self._tr_prev_close)
        )
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_len else None

        # 3. Zero-line crosses of the signal line.
        con1 = self._prev_signal is not None and self._prev_signal <= 0 and signal_line > 0
        con2 = self._prev_signal is not None and self._prev_signal >= 0 and signal_line < 0

        # 4. Trend state machine.
        if con1:
            self.uptrend = True
            self.signalflag = False
            self.dntrend = False
        if con2:
            self.uptrend = False
            self.buysetup = False
            self.signalflag = False
            self.dntrend = True

        if self.uptrend:
            if not self.signalflag:
                self.buysetup = True
                self.ctrend_low = low
            if macd < signal_line and low < self._prev_ctrend_low:
                self.ctrend_low = low

        # 5. Arm the entry / exit channel on the bar BuySetup first turned true.
        if self._prev_buysetup and not self._prev2_buysetup:
            if self._prev_close is not None and self._prev_aatr is not None:
                self.upperband = self._prev_close + cfg.entry_atr_pcnt * self._prev_aatr
                self.exitband = self._prev_close - cfg.exit_atr_pcnt * self._prev_aatr

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 6. ENTRY (open long): setup armed last bar + break of the upper band.
        if (
            not acted and mp_start == 0 and self._prev_buysetup
            and self.upperband is not None and high >= self.upperband
        ):
            entry_price = max(open_, self.upperband)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.buysetup = False
            self.signalflag = True
            signal, reason, acted = BUY, "enter_long", True

        # 7. EXIT (sell): trend-over, then trend-low band, then exit band.
        if not acted and mp_start == 1 and self.bars_since_entry > 0:
            trend_low_line = self._prev_ctrend_low - cfg.tick
            if self._prev_dntrend:
                self._sell()
                signal, reason, acted = SELL, "exit_downtrend", True
            elif (
                self.exitband is not None and low <= trend_low_line
                and trend_low_line >= self.exitband
            ):
                self._sell()
                signal, reason, acted = SELL, "exit_trend_low", True
            elif self.exitband is not None and low <= self.exitband:
                self._sell()
                signal, reason, acted = SELL, "exit_band", True

        # 8. Roll the prev-bar snapshots, then advance counters.
        self._prev_signal = signal_line
        self._prev_dntrend = self.dntrend
        self._prev2_buysetup = self._prev_buysetup
        self._prev_buysetup = self.buysetup
        self._prev_ctrend_low = self.ctrend_low
        self._prev_aatr = atr
        self._prev_close = close
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _sell(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
