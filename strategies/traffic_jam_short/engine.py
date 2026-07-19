"""Traffic Jam short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Same structural
pattern as ``trendscore_short`` / ``vwm_short``: a position-aware engine emitting
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short``). Single unit, no pyramiding.

Ported from the TradeBlazer ``Traffic_Jam_S`` system — fade an up-move in a
ranging market:

* ``ADX`` from Wilder's DMI (period ``dmi_n``) flags a ranging market;
* entry (short) when flat and ``CurrentBar > dmi_n`` and, on the previous bar,
  ``ADX[1] < adx_level`` AND ``ADX[1] < ADX[adx_lower_than_before + 1]`` (ADX
  falling) AND ``ConsecBarsCount[1] == consec_bars`` (the last ``consec_bars``
  bars each closed above their prior close) AND ``Vol > 0`` -> sell at Open, with
  a protective stop ``High[1] + protect_stop_atr_multi * ATR[1]``;
* exit: a time stop after ``proactive_stop_bars`` bars, else the protective stop
  (``High >= ProtectStop[1]``).

``[1]`` semantics preserved: the entry reads the previous bar's ADX / consec
count / high / ATR, and the exit is gated by ``MarketPosition == -1 And mp[1] ==
-1`` so an entry bar never exits.

Fidelity notes:

* ``ADX`` uses standard Wilder smoothing (``SF = 1/dmi_n``) with an SMA seed of
  the first ``dmi_n`` values (see :class:`_DmiAdx`). This converges to the
  TradeBlazer DMI ADX in steady state; only the first ~2*dmi_n warmup bars — well
  before any trade — differ from TradeBlazer's ``cumm/CurrentBar`` seed.
* ``AvgTrueRange`` for the protective stop is a simple mean of true range over
  ``atr_length`` (matches ``trend_breakout_atr`` / ``trendscore_short``; the
  TradeBlazer builtin uses Wilder smoothing). Kept Nautilus-free.
* TradeBlazer fills entry at ``Open`` and the protective exit at ``Max(Open,
  ProtectStop)``; on the shared string-signal path the fill is a market fill at
  the signal bar (same accepted limitation as ``vwm_short``).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, true_range, WilderDMI

from strategies.traffic_jam_short.config import TrafficJamShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


# ``_DmiAdx`` now lives in the shared library as ``WilderDMI`` (textbook Wilder
# seeding, distinct from ``WilderADX``). The module-level alias keeps the focused
# unit test's ``from ...engine import _DmiAdx`` working while removing the
# duplicated class body; the maths are byte-identical.
_DmiAdx = WilderDMI


class TrafficJamShortEngine:
    """Pure, position-aware Traffic Jam short engine (no Nautilus, no pandas)."""

    def __init__(self, config: TrafficJamShortConfig) -> None:
        self.cfg = config
        self._dmi = WilderDMI(config.dmi_n)

        # protective-stop ATR (simple mean of true range)
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._atr_prev_close: float | None = None

        # consecutive up-close tracking (CountIf(Close > Close[1], consec_bars))
        self._up_flags: deque[int] = deque(maxlen=config.consec_bars)
        self._consec_prev_close: float | None = None

        # ADX history for the [1] and [adx_lower_than_before+1] look-backs
        self._adx_hist: deque[float | None] = deque(maxlen=config.adx_lower_than_before + 2)

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.protect_stop: float | None = None
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_atr: float | None = None
        self._prev_high: float | None = None
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

        # 3. consecutive up-close count (this bar's CountIf).
        up = 1 if (self._consec_prev_close is not None and close > self._consec_prev_close) else 0
        self._up_flags.append(up)
        self._consec_prev_close = close
        consec_count = sum(self._up_flags) if len(self._up_flags) == cfg.consec_bars else None

        # previous-bar ADX look-backs: ADX[1] and ADX[adx_lower_than_before+1].
        need = cfg.adx_lower_than_before + 1
        prev_adx1 = self._adx_hist[-1] if len(self._adx_hist) >= 1 else None
        prev_adxN = self._adx_hist[-need] if len(self._adx_hist) >= need else None

        signal, reason = HOLD, "hold"
        entered = False

        # 4. ENTRY (open short): ranging + falling ADX + consec up-closes.
        if (
            self.position != -1
            and self.current_bar > cfg.dmi_n
            and prev_adx1 is not None
            and prev_adxN is not None
            and self._prev_atr is not None
            and self._prev_high is not None
            and self._prev_consec is not None
            and volume > 0
            and prev_adx1 < cfg.adx_level
            and prev_adx1 < prev_adxN
            and self._prev_consec == cfg.consec_bars
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.last_entry_price = open_  # TradeBlazer enters at Open
            self.protect_stop = self._prev_high + cfg.protect_stop_atr_multi * self._prev_atr
            signal, reason = SELL, "enter_short"
            entered = True

        # 5. EXIT: time stop, else protective stop (only when short >= one full bar).
        if self.position == -1 and self._prev_mp == -1 and volume > 0 and not entered:
            if self.bars_since_entry >= cfg.proactive_stop_bars:
                signal, reason = BUY, "exit_time_stop"
                self._flat()
            elif self.protect_stop is not None and high >= self.protect_stop:
                signal, reason = BUY, "exit_protect_stop"
                self._flat()

        # 6. Save this bar's values as the ``[1]`` snapshots, then advance counters.
        self._prev_atr = atr
        self._prev_high = high
        self._prev_consec = consec_count
        self._prev_mp = self.position
        self._adx_hist.append(adx)
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.protect_stop = None
        self.last_entry_price = None
