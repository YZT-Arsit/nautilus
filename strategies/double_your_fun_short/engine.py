"""DoubleYourFun short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``DoubleYourFun_S`` system:

* ``MA = Average(Close, AvgLength)``; the displaced MA ``DMA = MA[AvgDisplace]``
  (the MA value ``AvgDisplace`` bars ago);
* ``ConCrossOver = CrossOver(Close, DMA)``, ``ConCrossUnder = CrossUnder(Close,
  DMA)``; ``NthCon`` distances give bars-ago of the last up-cross
  (``BarsLastCrsOvr``) and the last two down-crosses (``BarsSecCrsUnd`` = 0 on a
  down-cross bar, ``BarsFstCrsUnd``);
* on a down-cross with ``BarsLastCrsOvr - BarsSecCrsUnd <= ValidBars2`` and
  ``BarsFstCrsUnd - BarsLastCrsOvr <= ValidBars1`` (the down / up / down pattern
  closed inside its windows) arm ``EntryFlag`` with ``EntryPoint = Low - tick`` and
  reset ``EntryCount = 0``;
* entry (short), while flat and ``EntryCount <= ValidBars3``: ``EntryFlag`` and
  ``Low <= EntryPoint`` and ``Vol > 0`` -> short at ``Min(Open, EntryPoint)``;
  otherwise ``EntryCount += 1`` (the window ages out);
* ``EntryFlag`` clears once short (bar-start ``MarketPosition == -1``) or the window
  expired (``EntryCount > ValidBars3``);
* exit (cover), once ``BarsSinceEntry > 0`` and ``Vol > 0``: with ``ReversalPrice =
  DMA[1] + tick`` and ``TrailStopPrice = Highest(High[1], TrailStopBars)``, ``High
  >= Min(ReversalPrice, TrailStopPrice)`` -> cover at ``Max(Open, Min(...))``.

Faithful TradeBlazer semantics preserved: the ``NthCon`` distances count back
**including** the current bar (a down-cross bar has ``BarsSecCrsUnd == 0``); crosses
read ``Close[1]``/``DMA[1]``; ``ReversalPrice`` reads ``DMA[1]`` and the trailing
stop ``Highest(High[1], N)`` excludes the current bar; ``MarketPosition`` uses the
bar-start position; the exit is gated by ``BarsSinceEntry > 0`` so entry and cover
never fire on one bar. There **is** a ``Vol > 0`` gate. ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import sma
from strategies.double_your_fun_short.config import DoubleYourFunShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class DoubleYourFunShortEngine:
    """Pure, position-aware DoubleYourFun short engine."""

    def __init__(self, config: DoubleYourFunShortConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.avg_length)
        self._ma_hist: deque[float | None] = deque(maxlen=config.avg_displace + 1)
        self._highs: deque[float] = deque(maxlen=config.trail_stop_bars)

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # cross bookkeeping (bar indices; None until seen)
        self._last_crsovr: int | None = None
        self._last_crsund: int | None = None
        self._second_crsund: int | None = None

        # armed-entry state (persistent)
        self.entry_flag = False
        self.entry_point: float | None = None
        self.entry_count = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots
        self._prev_close: float | None = None
        self._prev_dma: float | None = None

    def _sma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        return sma(list(self._closes)[-period:])

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
        if crossover:
            self._last_crsovr = cb
        if crossunder:
            self._second_crsund = self._last_crsund
            self._last_crsund = cb
        bars_last_crsovr = cb - self._last_crsovr if self._last_crsovr is not None else None
        bars_sec_crsund = cb - self._last_crsund if self._last_crsund is not None else None
        bars_fst_crsund = cb - self._second_crsund if self._second_crsund is not None else None

        # 4. Arm the entry on a valid down / up / down pattern.
        if (
            crossunder and bars_last_crsovr is not None
            and bars_sec_crsund is not None and bars_fst_crsund is not None
            and (bars_last_crsovr - bars_sec_crsund) <= cfg.valid_bars2
            and (bars_fst_crsund - bars_last_crsovr) <= cfg.valid_bars1
        ):
            self.entry_flag = True
            self.entry_point = low - cfg.tick
            self.entry_count = 0

        # 5. Exit levels (use previous-bar DMA and the prior highs).
        reversal = pdma + cfg.tick if pdma is not None else None
        trail = max(self._highs) if len(self._highs) >= 1 else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 6. ENTRY (open short): armed break of the setup low within the window.
        if mp_start == 0 and self.entry_count <= cfg.valid_bars3:
            if (
                self.entry_flag and self.entry_point is not None
                and low <= self.entry_point and volume > 0
            ):
                entry_price = min(open_, self.entry_point)
                self.position = -1
                self.bars_since_entry = 0
                self.entry_price = entry_price
                signal, reason, acted = SELL, "enter_short", True
            else:
                self.entry_count += 1

        # 7. Clear the armed flag once short or the window has expired.
        if mp_start == -1 or self.entry_count > cfg.valid_bars3:
            self.entry_flag = False

        # 8. EXIT (cover): break of the nearer of the reversal / trailing stop.
        if (
            not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0
            and reversal is not None and trail is not None
        ):
            stop = min(reversal, trail)
            if high >= stop:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_stop", True

        # 9. Roll snapshots and advance counters.
        self._prev_close = close
        self._prev_dma = dma
        self._highs.append(high)
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
