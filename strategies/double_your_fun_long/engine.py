"""DoubleYourFun long — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``DoubleYourFun_L`` system (long mirror of
``DoubleYourFun_S``):

* ``MA = Average(Close, AvgLength)``; displaced ``DMA = MA[AvgDisplace]``;
* ``ConCrossOver = CrossOver(Close, DMA)``, ``ConCrossUnder = CrossUnder(Close,
  DMA)``; ``NthCon`` distances give bars-ago of the last down-cross
  (``BarsLastCrsUnd``) and the last two up-crosses (``BarsSecCrsOvr`` = 0 on an
  up-cross bar, ``BarsFstCrsOvr``);
* on an up-cross with ``BarsLastCrsUnd - BarsSecCrsOvr <= ValidBars2`` and
  ``BarsFstCrsOvr - BarsLastCrsUnd <= ValidBars1`` (the up / down / up pattern
  closed inside its windows) arm ``EntryFlag`` with ``EntryPoint = High + tick`` and
  reset ``EntryCount = 0``;
* entry (long), while flat and ``EntryCount <= ValidBars3``: ``EntryFlag`` and
  ``High >= EntryPoint`` and ``Vol > 0`` -> long at ``Max(Open, EntryPoint)``;
  otherwise ``EntryCount += 1`` (the window ages out);
* ``EntryFlag`` clears once long (bar-start ``MarketPosition == 1``) or the window
  expired (``EntryCount > ValidBars3``);
* exit (sell), once ``BarsSinceEntry > 0`` and ``Vol > 0``: with ``ReversalPrice =
  DMA[1] - tick`` and ``TrailStopPrice = Lowest(Low[1], TrailStopBars)``, ``Low <=
  Max(ReversalPrice, TrailStopPrice)`` -> sell at ``Min(Open, Max(...))``.

Faithful TradeBlazer semantics preserved: the ``NthCon`` distances count back
**including** the current bar (an up-cross bar has ``BarsSecCrsOvr == 0``); crosses
read ``Close[1]``/``DMA[1]``; ``ReversalPrice`` reads ``DMA[1]`` and the trailing
stop ``Lowest(Low[1], N)`` excludes the current bar; ``MarketPosition`` uses the
bar-start position; the exit is gated by ``BarsSinceEntry > 0`` so entry and sell
never fire on one bar. There **is** a ``Vol > 0`` gate. ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from strategies.double_your_fun_long.config import DoubleYourFunLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class DoubleYourFunLongEngine:
    """Pure, position-aware DoubleYourFun long engine."""

    def __init__(self, config: DoubleYourFunLongConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.avg_length)
        self._ma_hist: deque[float | None] = deque(maxlen=config.avg_displace + 1)
        self._lows: deque[float] = deque(maxlen=config.trail_stop_bars)

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # cross bookkeeping (bar indices; None until seen)
        self._last_crsund: int | None = None
        self._last_crsovr: int | None = None
        self._second_crsovr: int | None = None

        # armed-entry state (persistent)
        self.entry_flag = False
        self.entry_point: float | None = None
        self.entry_count = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots
        self._prev_close: float | None = None
        self._prev_dma: float | None = None

    def _sma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        return sum(list(self._closes)[-period:]) / period

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._bar += 1
        cb = self._bar

        # 1. MA and the displaced MA (MA value AvgDisplace bars ago).
        self._closes.append(close)
        ma = self._sma(cfg.avg_length)
        self._ma_hist.append(ma)
        dma = (
            self._ma_hist[0]
            if len(self._ma_hist) == cfg.avg_displace + 1 and self._ma_hist[0] is not None
            else None
        )

        # 2. Close vs DMA crosses (need previous close and DMA).
        pc, pdma = self._prev_close, self._prev_dma
        crossover = pc is not None and pdma is not None and dma is not None and pc <= pdma and close > dma
        crossunder = pc is not None and pdma is not None and dma is not None and pc >= pdma and close < dma

        # 3. Update the cross history (current bar included), then compute distances.
        if crossunder:
            self._last_crsund = cb
        if crossover:
            self._second_crsovr = self._last_crsovr
            self._last_crsovr = cb
        bars_last_crsund = cb - self._last_crsund if self._last_crsund is not None else None
        bars_sec_crsovr = cb - self._last_crsovr if self._last_crsovr is not None else None
        bars_fst_crsovr = cb - self._second_crsovr if self._second_crsovr is not None else None

        # 4. Arm the entry on a valid up / down / up pattern.
        if (
            crossover and bars_last_crsund is not None
            and bars_sec_crsovr is not None and bars_fst_crsovr is not None
            and (bars_last_crsund - bars_sec_crsovr) <= cfg.valid_bars2
            and (bars_fst_crsovr - bars_last_crsund) <= cfg.valid_bars1
        ):
            self.entry_flag = True
            self.entry_point = high + cfg.tick
            self.entry_count = 0

        # 5. Exit levels (use previous-bar DMA and the prior lows).
        reversal = pdma - cfg.tick if pdma is not None else None
        trail = min(self._lows) if len(self._lows) >= 1 else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 6. ENTRY (open long): armed break of the setup high within the window.
        if mp_start == 0 and self.entry_count <= cfg.valid_bars3:
            if (
                self.entry_flag and self.entry_point is not None
                and high >= self.entry_point and volume > 0
            ):
                entry_price = max(open_, self.entry_point)
                self.position = 1
                self.bars_since_entry = 0
                self.entry_price = entry_price
                signal, reason, acted = BUY, "enter_long", True
            else:
                self.entry_count += 1

        # 7. Clear the armed flag once long or the window has expired.
        if mp_start == 1 or self.entry_count > cfg.valid_bars3:
            self.entry_flag = False

        # 8. EXIT (sell): break of the farther of the reversal / trailing stop.
        if (
            not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0
            and reversal is not None and trail is not None
        ):
            stop = max(reversal, trail)
            if low <= stop:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_stop", True

        # 9. Roll snapshots and advance counters.
        self._prev_close = close
        self._prev_dma = dma
        self._lows.append(low)
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
