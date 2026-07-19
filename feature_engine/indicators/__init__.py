"""Shared streaming technical-indicator primitives (framework-free).

The single home for the low-level indicator maths that strategy engines used to
re-implement inline (SMA, EMA/XAverage, ATR/True Range, Wilder ADX, rolling std-dev, Donchian
highest/lowest, half-up rounding). Strategies call these instead of copy-pasting
the maths, so the definitions live in **one** place and stay consistent.

Design contract (mirrors ``data_engine`` / ``feature_engine.compute.feature_lib``):

* **Pure Python** — no ``nautilus_trader``, no ``pandas``, no ``polars``; only the
  standard library. Importing this subpackage is cheap.
* **TB-faithful** — the maths reproduce the TradeBlazer semantics the strategy
  ports rely on exactly (EMA seeds on the first value with ``alpha=2/(N+1)``;
  Wilder ADX seeds at ``CurrentBar==N`` from a simple average then Wilder-smooths;
  ``rolling_std`` exposes both sample ``ddof=1`` and population ``ddof=0`` divisors;
  ``round_half_up`` matches TB ``Round(x,0)``).
* **Stateful classes** (``Ema``, ``WilderADX``) advance one value per ``update``;
  **stateless helpers** (``sma``, ``simple_atr``, ``true_range``,
  ``rolling_std``, ``highest``, ``lowest``) operate
  on a caller-provided window sequence.

This is a public surface — import from here, not from the submodules::

    from feature_engine.indicators import Ema, WilderADX, sma, rolling_std, highest, lowest

Two Wilder ADX variants coexist deliberately: ``WilderADX`` (ADXandMAChannel DMI
seeding) and ``WilderDMI`` (textbook Wilder seeding, used by ``traffic_jam``). They
seed differently and are **not** interchangeable — see ``wilder_dmi`` for why.
"""
from feature_engine.indicators.atr import SimpleAtr, simple_atr, true_range
from feature_engine.indicators.cross import cross_over, cross_under
from feature_engine.indicators.ema import Ema
from feature_engine.indicators.momentum import Momentum
from feature_engine.indicators.sma import sma, sma_last
from feature_engine.indicators.wilder_adx import WilderADX
from feature_engine.indicators.wilder_dmi import WilderDMI
from feature_engine.indicators.window import (
    highest,
    lowest,
    rolling_std,
    round_half_up,
)

__all__ = [
    "Ema",
    "Momentum",
    "SimpleAtr",
    "cross_over",
    "cross_under",
    "simple_atr",
    "WilderADX",
    "WilderDMI",
    "sma",
    "sma_last",
    "rolling_std",
    "highest",
    "lowest",
    "round_half_up",
    "true_range",
]
