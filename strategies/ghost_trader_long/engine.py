"""Ghost Trader long — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``GhostTrader_L`` system — the long mirror of
``ghost_trader_short``:

* ``AvgValue1 = XAverage(Close, FastLength)``; ``AvgValue2 = XAverage(Close,
  SlowLength)`` (EMAs); a Wilder ``RSIValue = 50*(NetChgAvg/TotChgAvg + 1)``; and
  the 20-bar Donchian channel ``ExitLoBand = Lowest(Low, 20)``;
* a **simulated** long is tracked continuously via ``myPosition`` /
  ``myEntryPrice`` / ``myExitPrice`` / ``myProfit``:
  - sim entry (long): ``myPosition == 0`` and ``myPosition[1] == 0``,
    ``AvgValue1[1] > AvgValue2[1]``, ``RSIValue[1] < OverBought`` and ``High >=
    High[1]`` -> ``myEntryPrice = Max(Open, High[1])``, ``myPosition = 1``;
  - sim exit: ``myPosition == 1`` and ``Low <= ExitLoBand[1]`` -> ``myExitPrice =
    Min(Open, ExitLoBand[1])``, ``myProfit = myExitPrice - myEntryPrice``,
    ``myPosition = 0``;
* a **real** order is placed only in step with the simulated one AND gated on the
  prior simulated result: the real long fires at sim entry **only if** ``myProfit
  < 0`` (the last simulated trade lost); the real sell fires at sim exit whenever
  a real long is open.

Faithful TradeBlazer semantics preserved: the setup reads ``AvgValue1[1]`` /
``AvgValue2[1]`` / ``RSIValue[1]`` / ``ExitLoBand[1]`` / ``High[1]`` /
``myPosition[1]`` (previous-bar values snapshotted before the roll); the simulated
position / profit persist as running state and drive the ``myProfit < 0`` gate.
Real entry and sell can never fire on one bar (the sim setups are mutually
exclusive). ``XAverage`` is a standard EMA (alpha = 2/(n+1), seeded on the first
bar); the RSI uses Wilder smoothing.
"""
from __future__ import annotations

from collections import deque

from strategies.ghost_trader_long.config import GhostTraderLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class GhostTraderLongEngine:
    """Pure, position-aware Ghost Trader long engine."""

    def __init__(self, config: GhostTraderLongConfig) -> None:
        self.cfg = config
        self._closes: list[float] = []                       # for Close[1] / Close[Length]
        self._abs_diffs: deque[float] = deque(maxlen=config.rsi_length)
        self._highs: deque[float] = deque(maxlen=config.donchian_length)
        self._lows_d: deque[float] = deque(maxlen=config.donchian_length)

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # running indicators
        self._ema_fast: float | None = None
        self._ema_slow: float | None = None
        self._net_chg_avg: float | None = None
        self._tot_chg_avg: float | None = None

        # simulated position state
        self.my_position = 0
        self.my_entry_price = 0.0
        self.my_exit_price = 0.0
        self.my_profit = 0.0

        # real position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_avg1: float | None = None
        self._prev_avg2: float | None = None
        self._prev_rsi: float | None = None
        self._prev_exit_lo: float | None = None
        self._prev_high: float | None = None
        self._prev_close: float | None = None
        self._prev_my_position = 0

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._bar += 1
        cb = self._bar

        # 1. EMAs (XAverage: alpha = 2/(n+1), seeded on the first bar).
        if self._ema_fast is None:
            self._ema_fast = close
            self._ema_slow = close
        else:
            self._ema_fast += (2 / (cfg.fast_length + 1)) * (close - self._ema_fast)
            self._ema_slow += (2 / (cfg.slow_length + 1)) * (close - self._ema_slow)
        avg1, avg2 = self._ema_fast, self._ema_slow

        # 2. Wilder RSI (reformulated as 50*(NetChgAvg/TotChgAvg + 1)).
        self._closes.append(close)
        change = close - self._prev_close if self._prev_close is not None else 0.0
        if self._prev_close is not None:
            self._abs_diffs.append(abs(change))
        L = cfg.rsi_length
        net = tot = None
        if cb == L:
            net = (close - self._closes[-(L + 1)]) / L
            tot = sum(self._abs_diffs) / L if len(self._abs_diffs) == L else None
        elif cb > L and self._net_chg_avg is not None and self._tot_chg_avg is not None:
            sf = 1 / L
            net = self._net_chg_avg + sf * (change - self._net_chg_avg)
            tot = self._tot_chg_avg + sf * (abs(change) - self._tot_chg_avg)
        if tot is None or net is None:
            rsi = None
        elif tot == 0:
            rsi = 50.0
        else:
            rsi = 50 * (net / tot + 1)

        # 3. Donchian channel (current bar inclusive).
        self._highs.append(high)
        self._lows_d.append(low)
        exit_lo = min(self._lows_d)

        prev_my_position = self._prev_my_position
        signal, reason = HOLD, "hold"
        acted = False

        # 4. SIM EXIT (+ real sell): break below the prior Donchian lower channel.
        if (
            prev_my_position == 1 and self._prev_exit_lo is not None
            and low <= self._prev_exit_lo
        ):
            self.my_exit_price = min(open_, self._prev_exit_lo)
            self.my_profit = self.my_exit_price - self.my_entry_price
            self.my_position = 0
            if self.position == 1:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_sell", True

        # 5. SIM ENTRY (+ conditional real long): trend-up + RSI + new high.
        if (
            self.my_position == 0 and prev_my_position == 0
            and self._prev_avg1 is not None and self._prev_avg2 is not None
            and self._prev_rsi is not None and self._prev_high is not None
            and self._prev_avg1 > self._prev_avg2 and self._prev_rsi < cfg.over_bought
            and high >= self._prev_high
        ):
            self.my_entry_price = max(open_, self._prev_high)
            self.my_position = 1
            # Real long only after a losing simulated trade.
            if not acted and self.my_profit < 0 and self.position == 0:
                self.position = 1
                self.bars_since_entry = 0
                self.entry_price = self.my_entry_price
                signal, reason, acted = BUY, "enter_long", True

        # 6. Roll snapshots / running state, then advance counters.
        self._prev_avg1 = avg1
        self._prev_avg2 = avg2
        self._prev_rsi = rsi
        self._prev_exit_lo = exit_lo
        self._prev_high = high
        self._prev_close = close
        self._prev_my_position = self.my_position
        if net is not None:
            self._net_chg_avg = net
        if tot is not None:
            self._tot_chg_avg = tot
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
