"""Focused tests for the Four-MA Crossover short engine.

Pure Python; no Nautilus, no network. Two SMA pairs (5/20 entry, 3/10 exit): a
short opens when both pairs are bearishly arranged and price makes a lower low; it
covers when the 3/10 exit pair flips bullish. Runnable via ``pytest
tests_platform -k four_ma_crossover``.
"""
from __future__ import annotations

from strategies.four_ma_crossover_short.config import FourMaCrossoverShortConfig as Cfg
from strategies.four_ma_crossover_short.engine import FourMaCrossoverShortEngine as Engine


def _cfg():
    return Cfg(se_fast=5, se_slow=20, sx_fast=3, sx_slow=10,
               le_fast=5, le_slow=20, lx_fast=3, lx_slow=10, min_bars=25)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _downtrend_then_bounce():
    bars = []
    p = 140.0
    for _ in range(30):
        p -= 1.0
        bars.append((p + 0.3, p + 0.4, p - 0.3, p))     # steady decline -> all MAs bearish
    bars.append((p, p + 0.1, p - 1.5, p - 1.2))          # lower low -> short
    p -= 1.2
    for _ in range(8):
        p += 2.0
        bars.append((p - 0.3, p + 0.3, p - 0.4, p))     # bounce -> ma3 > ma10 -> cover
    return bars


def test_entry_and_exit():
    sigs = _signals(Engine(_cfg()), _downtrend_then_bounce())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_ma_cross") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _downtrend_then_bounce(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_uptrend():
    # A rising series arranges the MAs bullishly -> no short.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(60)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_min_bars_gate_blocks_early_entry():
    # With a high min_bars the entry is suppressed even once the MAs are bearish.
    eng = Engine(Cfg(se_fast=5, se_slow=20, sx_fast=3, sx_slow=10,
                     le_fast=5, le_slow=20, lx_fast=3, lx_slow=10, min_bars=1000))
    sigs = _signals(eng, _downtrend_then_bounce())
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
