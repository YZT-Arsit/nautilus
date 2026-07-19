"""Reference Deviation System short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Reference_Deviation_System_S`` system:

* ``RMA = Average(Close, rma_len)`` (SMA);
* ``DRD = Close - RMA`` (deviation of price from the MA);
* ``NDV = Summation(DRD, rma_len)``, ``TDV = Summation(Abs(DRD), rma_len)``;
* ``RDV = 100 * NDV / TDV`` when ``TDV > 0`` (a -100..100 oscillator: +100 when
  price is above the MA on every bar of the window, -100 when below on every bar,
  ~0 when it straddles the MA);
* entry (short): flat, ``RDV[1] < et_short``, ``Vol > 0`` -> short at Open;
* exit (cover): short, ``RDV[1] > 0``, ``Vol > 0`` -> cover at Open.

Faithful TradeBlazer semantics preserved: both decisions read the **previous**
bar's RDV (``RDV[1]``); ``MarketPosition == 0`` / ``== -1`` uses the bar-start
position, so an entry and a cover never both fill on one bar (and the entry
``RDV < et_short`` and exit ``RDV > 0`` conditions are mutually exclusive anyway).
When ``TDV == 0`` (price exactly on the MA across the whole window) RDV holds its
previous value, matching the TradeBlazer series' conditional assignment.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import sma
from strategies.reference_deviation_short.config import ReferenceDeviationShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class ReferenceDeviationShortEngine:
    """Pure, position-aware Reference Deviation System short engine."""

    def __init__(self, config: ReferenceDeviationShortConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.rma_len)  # for RMA = SMA
        self._drd: deque[float] = deque(maxlen=config.rma_len)     # for NDV / TDV sums

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0

        self._rdv: float | None = None    # current RDV (held across TDV==0 bars)
        self._prev_rdv: float | None = None  # RDV[1] the decisions read

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        """Legacy immediate-fill wrapper retained for baseline compatibility."""
        signal, reason = self.generate_signal(
            open_, high, low, close, volume,
            position=self.position,
            bars_since_entry=self.bars_since_entry,
        )
        if signal == SELL:
            self.position = -1
            self.bars_since_entry = 0
        elif signal == BUY:
            self.position = 0
            self.bars_since_entry = 0
        if self.position == -1:
            self.bars_since_entry += 1
        return signal, reason

    def generate_signal(
        self, open_: float, high: float, low: float, close: float, volume: float,
        *, position: int, bars_since_entry: int,
    ):
        """Update RDV history and return a signal without assuming a fill."""
        cfg = self.cfg
        self.current_bar += 1

        # 1. current-bar indicators (decisions still read the prev-bar RDV).
        self._closes.append(close)
        if len(self._closes) == cfg.rma_len:
            rma = sma(self._closes)
            self._drd.append(close - rma)
        if len(self._drd) == cfg.rma_len:
            ndv = sum(self._drd)
            tdv = sum(abs(x) for x in self._drd)
            if tdv > 0:
                self._rdv = 100.0 * ndv / tdv
            # else: TDV == 0 -> RDV keeps its previous value (TradeBlazer semantics)

        mp_start = position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open short): previous RDV strongly negative.
        if (
            not acted and mp_start == 0
            and self._prev_rdv is not None and self._prev_rdv < cfg.et_short
            and volume > 0
        ):
            signal, reason, acted = SELL, "enter_short", True

        # 3. EXIT (cover): previous RDV back above zero.
        if (
            not acted and mp_start == -1
            and self._prev_rdv is not None and self._prev_rdv > 0
            and volume > 0
        ):
            signal, reason, acted = BUY, "exit_cover", True

        # 4. Roll the prev-bar RDV. Position/counters are execution-adapter state.
        self._prev_rdv = self._rdv

        return signal, reason
