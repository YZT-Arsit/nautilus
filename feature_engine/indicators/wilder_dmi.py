"""Wilder DMI / ADX with the *standard* Wilder seeding (SF = 1/n).

This is a **distinct** operator from :class:`~feature_engine.indicators.WilderADX`.
Both compute a Wilder-smoothed ADX, but they seed differently and therefore
produce different values during (and, for the ADX line, well past) warm-up:

* ``WilderADX`` reproduces the ``ADXandMAChannelSys`` DMI block — +DM/-DM/TR seed
  at ``CurrentBar == n`` and the ADX line seeds as the *cumulative mean* of every
  DMI value from ``CurrentBar == 1``.
* ``WilderDMI`` (this class) uses the textbook Wilder seeding — +DM/-DM/TR seed as
  the SMA of the first ``n`` values, and the ADX line seeds as the SMA of the
  first ``n`` DX values, then both Wilder-smooth (``avg += SF*(x - avg)``). This is
  what the ``traffic_jam`` systems use.

They converge in steady state but are not interchangeable; keep them separate so
each strategy's numbers are preserved exactly. Pure Python; no framework
dependency.
"""
from __future__ import annotations


class WilderDMI:
    """Wilder DMI / ADX (``SF = 1/n``); returns the current ADX or None while warming.

    Standard Wilder seeding: the +DM / -DM / TR averages seed as the SMA of the
    first ``n`` values then smooth as ``avg += SF*(x - avg)``; ADX seeds as the
    SMA of the first ``n`` DX values then smooths the same way. Converges to the
    TradeBlazer DMI ADX in steady state.
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self.sf = 1.0 / n
        self._ph: float | None = None
        self._pl: float | None = None
        self._pc: float | None = None
        self._pdm: list[float] = []
        self._mdm: list[float] = []
        self._tr: list[float] = []
        self.avg_pdm: float | None = None
        self.avg_mdm: float | None = None
        self.svolty: float | None = None
        self._dx: list[float] = []
        self.adx: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._pc is None:
            self._ph, self._pl, self._pc = high, low, close
            return None

        tr = max(high - low, abs(high - self._pc), abs(low - self._pc))
        up_move = high - self._ph
        down_move = self._pl - low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        self._ph, self._pl, self._pc = high, low, close

        if self.avg_pdm is None:
            self._pdm.append(plus_dm)
            self._mdm.append(minus_dm)
            self._tr.append(tr)
            if len(self._tr) < self.n:
                return None
            self.avg_pdm = sum(self._pdm) / self.n
            self.avg_mdm = sum(self._mdm) / self.n
            self.svolty = sum(self._tr) / self.n
        else:
            self.avg_pdm += self.sf * (plus_dm - self.avg_pdm)
            self.avg_mdm += self.sf * (minus_dm - self.avg_mdm)
            self.svolty += self.sf * (tr - self.svolty)

        if self.svolty > 0:
            plus_di = 100.0 * self.avg_pdm / self.svolty
            minus_di = 100.0 * self.avg_mdm / self.svolty
        else:
            plus_di = minus_di = 0.0
        divisor = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / divisor if divisor > 0 else 0.0

        if self.adx is None:
            self._dx.append(dx)
            if len(self._dx) == self.n:
                self.adx = sum(self._dx) / self.n
        else:
            self.adx += self.sf * (dx - self.adx)
        return self.adx
