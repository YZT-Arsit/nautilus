"""Traffic Jam long — pure decision engine (position-aware, offline-testable).

Long-side mirror of ``strategies/traffic_jam_short/engine.py``. Holds **only**
the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL
flattens it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Traffic_Jam_L`` system — fade a down-move in a
ranging market:

* ``ADX`` from Wilder's DMI (period ``dmi_n``) flags a ranging market;
* entry (long) when flat and ``CurrentBar > dmi_n`` and, on the previous bar,
  ``ADX[1] < adx_level`` AND ``ADX[1] < ADX[adx_lower_than_before + 1]`` (ADX
  falling) AND ``ConsecBarsCount[1] == consec_bars`` (the last ``consec_bars``
  bars each closed below their prior close) AND ``Vol > 0`` -> buy at Open, with a
  protective stop ``Low[1] - protect_stop_atr_multi * ATR[1]``;
* exit: a time stop after ``proactive_stop_bars`` bars, else the protective stop
  (``Low <= ProtectStop[1]``).

``[1]`` semantics preserved; exit gated by ``MarketPosition == 1 And mp[1] == 1``
so an entry bar never exits. Fidelity notes mirror the short engine (standard
Wilder ADX seeding; simple-mean ATR; Open / Min(Open, ProtectStop) fills on the
string-signal path).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, true_range, WilderDMI

from strategies.traffic_jam_long.config import TrafficJamLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


# ``_DmiAdx`` now lives in the shared library as ``WilderDMI`` (textbook Wilder
# seeding, distinct from ``WilderADX``). The module-level alias keeps the focused
# unit test's ``from ...engine import _DmiAdx`` working while removing the
# duplicated class body; the maths are byte-identical.
_DmiAdx = WilderDMI


class TrafficJamLongEngine:
    """Pure, position-aware Traffic Jam long engine (no Nautilus, no pandas)."""

    def __init__(self, config: TrafficJamLongConfig) -> None:
        self.cfg = config
        self._dmi = WilderDMI(config.dmi_n)

        # protective-stop ATR (simple mean of true range)
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._atr_prev_close: float | None = None

        # consecutive down-close tracking (CountIf(Close < Close[1], consec_bars))
        self._down_flags: deque[int] = deque(maxlen=config.consec_bars)
        self._consec_prev_close: float | None = None

        # ADX history for the [1] and [adx_lower_than_before+1] look-backs
        self._adx_hist: deque[float | None] = deque(maxlen=config.adx_lower_than_before + 2)

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.protect_stop: float | None = None
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_atr: float | None = None
        self._prev_low: float | None = None
        self._prev_consec: int | None = None
        self._prev_mp = 0

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. ADX from Wilder DMI.
        adx = self._dmi.update(high, low, close)

        # 2. protective-stop ATR (simple mean of true range).
        tr = true_range(high, low, self._atr_prev_close)
        self._trs.append(tr)
        self._atr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_length)

        # 3. consecutive down-close count (this bar's CountIf).
        down = 1 if (self._consec_prev_close is not None and close < self._consec_prev_close) else 0
        self._down_flags.append(down)
        self._consec_prev_close = close
        consec_count = sum(self._down_flags) if len(self._down_flags) == cfg.consec_bars else None

        # previous-bar ADX look-backs: ADX[1] and ADX[adx_lower_than_before+1].
        need = cfg.adx_lower_than_before + 1
        prev_adx1 = self._adx_hist[-1] if len(self._adx_hist) >= 1 else None
        prev_adxN = self._adx_hist[-need] if len(self._adx_hist) >= need else None

        signal, reason = HOLD, "hold"
        entered = False

        # 4. ENTRY (open long): ranging + falling ADX + consec down-closes.
        if (
            self.position != 1
            and self.current_bar > cfg.dmi_n
            and prev_adx1 is not None
            and prev_adxN is not None
            and self._prev_atr is not None
            and self._prev_low is not None
            and self._prev_consec is not None
            and volume > 0
            and prev_adx1 < cfg.adx_level
            and prev_adx1 < prev_adxN
            and self._prev_consec == cfg.consec_bars
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.last_entry_price = open_  # TradeBlazer enters at Open
            self.protect_stop = self._prev_low - cfg.protect_stop_atr_multi * self._prev_atr
            signal, reason = BUY, "enter_long"
            entered = True

        # 5. EXIT: time stop, else protective stop (only when long >= one full bar).
        if self.position == 1 and self._prev_mp == 1 and volume > 0 and not entered:
            if self.bars_since_entry >= cfg.proactive_stop_bars:
                signal, reason = SELL, "exit_time_stop"
                self._flat()
            elif self.protect_stop is not None and low <= self.protect_stop:
                signal, reason = SELL, "exit_protect_stop"
                self._flat()

        # 6. Save this bar's values as the ``[1]`` snapshots, then advance counters.
        self._prev_atr = atr
        self._prev_low = low
        self._prev_consec = consec_count
        self._prev_mp = self.position
        self._adx_hist.append(adx)
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.protect_stop = None
        self.last_entry_price = None
