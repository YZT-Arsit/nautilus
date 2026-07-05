"""Focused tests for the Four-MA Crossover long engine.

Pure Python; no Nautilus, no network. Two SMA pairs (5/20 entry, 3/10 exit): a
long opens when both pairs are bullishly arranged and price makes a higher high;
it sells when the 3/10 exit pair flips bearish. Runnable via ``pytest
tests_platform -k four_ma_crossover``.
"""
from __future__ import annotations

from strategies.four_ma_crossover_long.config import FourMaCrossoverLongConfig as Cfg
from strategies.four_ma_crossover_long.engine import FourMaCrossoverLongEngine as Engine


def _cfg():
    return Cfg(le_fast=5, le_slow=20, lx_fast=3, lx_slow=10,
              se_fast=5, se_slow=20, sx_fast=3, sx_slow=10, min_bars=25)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _uptrend_then_dip():
    bars = []
    p = 80.0
    for _ in range(30):
        p += 1.0
        bars.append((p - 0.3, p + 0.3, p - 0.4, p))     # steady climb -> all MAs bullish
    bars.append((p, p + 1.5, p - 0.1, p + 1.2))          # higher high -> long
    p += 1.2
    for _ in range(8):
        p -= 2.0
        bars.append((p + 0.3, p + 0.4, p - 0.3, p))     # dip -> ma3 < ma10 -> sell
    return bars


def test_entry_and_exit():
    sigs = _signals(Engine(_cfg()), _uptrend_then_dip())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_ma_cross") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _uptrend_then_dip(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_downtrend():
    # A falling series arranges the MAs bearishly -> no long.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(160 - i, 160.3 - i, 159.7 - i, 160 - i) for i in range(60)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_min_bars_gate_blocks_early_entry():
    # With a high min_bars the entry is suppressed even once the MAs are bullish.
    eng = Engine(Cfg(le_fast=5, le_slow=20, lx_fast=3, lx_slow=10,
                     se_fast=5, se_slow=20, sx_fast=3, sx_slow=10, min_bars=1000))
    sigs = _signals(eng, _uptrend_then_dip())
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
