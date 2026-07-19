"""Wilder DMI / ADX (matching the TradeBlazer DMI block used by the ADX systems).

Reproduces the ``ADXandMAChannelSys`` DMI computation exactly: the +DM / -DM /
TrueRange averages are seeded at ``CurrentBar == n`` from a simple average of the
first ``n`` values, then Wilder-smoothed with ``SF = 1/n``. ``update`` returns the
current ``oADX`` (``sADX``) value, or ``None`` until it is defined
(``CurrentBar >= 1``). ``oADXR`` / the ADX-average period are not computed here —
the strategies that use this read only ``ADXValue`` and its prior value.

Pure Python; no framework dependency.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators.atr import true_range


class WilderADX:
    """Wilder DMI/ADX; ``update(high, low, close)`` -> current ADX or ``None``."""

    def __init__(self, n: int) -> None:
        if n <= 0:
            raise ValueError("n must be > 0.")
        self.n = n
        self.sf = 1.0 / n
        self.current_bar = -1
        self.avg_plus: float | None = None
        self.avg_minus: float | None = None
        self.svolty: float | None = None
        self.sadx: float | None = None
        self.cumm = 0.0
        self._high_hist: deque[float] = deque(maxlen=n + 1)
        self._low_hist: deque[float] = deque(maxlen=n + 1)
        self._tr_hist: deque[float] = deque(maxlen=n)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        self.current_bar += 1
        cb = self.current_bar

        tr = true_range(high, low, self._prev_close)
        self._high_hist.append(high)
        self._low_hist.append(low)
        self._tr_hist.append(tr)

        if cb == self.n:
            sum_plus = sum_minus = sum_tr = 0.0
            for i in range(self.n):
                hi, hi1 = self._high_hist[-1 - i], self._high_hist[-2 - i]
                lo, lo1 = self._low_hist[-1 - i], self._low_hist[-2 - i]
                upper, lower = hi - hi1, lo1 - lo
                if upper > lower and upper > 0:
                    sum_plus += upper
                elif lower > upper and lower > 0:
                    sum_minus += lower
                sum_tr += self._tr_hist[-1 - i]
            self.avg_plus = sum_plus / self.n
            self.avg_minus = sum_minus / self.n
            self.svolty = sum_tr / self.n
        elif cb > self.n:
            upper = high - self._prev_high
            lower = self._prev_low - low
            plus = minus = 0.0
            if upper > lower and upper > 0:
                plus = upper
            elif lower > upper and lower > 0:
                minus = lower
            self.avg_plus += self.sf * (plus - self.avg_plus)
            self.avg_minus += self.sf * (minus - self.avg_minus)
            self.svolty += self.sf * (tr - self.svolty)

        if self.svolty is not None and self.svolty > 0:
            odmi_plus = 100.0 * self.avg_plus / self.svolty
            odmi_minus = 100.0 * self.avg_minus / self.svolty
        else:
            odmi_plus = odmi_minus = 0.0
        divisor = odmi_plus + odmi_minus
        sdmi = 100.0 * abs(odmi_plus - odmi_minus) / divisor if divisor > 0 else 0.0
        self.cumm += sdmi

        adx: float | None = None
        if cb > 0:
            if cb <= self.n:
                self.sadx = self.cumm / cb
            else:
                self.sadx += self.sf * (sdmi - self.sadx)
            adx = self.sadx

        self._prev_high, self._prev_low, self._prev_close = high, low, close
        return adx
