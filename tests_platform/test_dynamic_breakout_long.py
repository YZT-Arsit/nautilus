"""Focused tests for the Dynamic Breakout II long engine.

Pure Python; no Nautilus, no network. A volatility-adaptive Bollinger + Donchian
system: a bar closing over the upper band with a break of the Donchian upper buys;
the position sells on a cross back below the adaptive mid-line (the liq exit).
Runnable via ``pytest tests_platform -k dynamic_breakout``.

Note on ``exit_reverse``: the reverse sell fires only if, while long, a prior close
drops under the *lower* Bollinger band and price breaks the Donchian *lower*. Any
drop that far first crosses the mid-line, so the liq sell (checked at the much
nearer mid-line) always preempts it. The branch is kept for source parity but is
effectively unreachable with the default levels, so it is not asserted here.
"""
from __future__ import annotations

import random

from strategies.dynamic_breakout_long.config import DynamicBreakoutLongConfig as Cfg
from strategies.dynamic_breakout_long.engine import DynamicBreakoutLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _quiet_then_rise_then_pullback():
    bars = []
    p = 100.0
    for i in range(70):                      # quiet range -> tight bands
        p += 0.3 if i % 2 == 0 else -0.3
        bars.append((p, p + 0.4, p - 0.4, p))
    for _ in range(10):                      # sharp rise -> break upper band + channel
        p += 2.5
        bars.append((p - 0.5, p + 0.6, p - 0.6, p))
    for _ in range(20):                      # pull back down to the mid-line
        p -= 1.5
        bars.append((p + 0.3, p + 0.4, p - 0.5, p))
    return bars


def test_entry_and_exit_liq():
    sigs = _signals(Engine(Cfg()), _quiet_then_rise_then_pullback())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_liq") in sigs


def test_no_entry_in_pure_downtrend():
    # A relentless fall never closes above the upper band -> no long.
    eng = Engine(Cfg())
    sigs = _signals(eng, [(160 - i, 160.4 - i, 159.5 - i, 160 - i) for i in range(120)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_no_vol_gate_still_runs():
    # No Vol > 0 gate in this system, so zero volume does not by itself block
    # trades; the engine must still process a full stream without raising.
    eng = Engine(Cfg())
    _signals(eng, _quiet_then_rise_then_pullback(), volume=0.0)
    assert eng.position in (0, 1)


def test_adaptive_lookback_stays_clamped():
    # lookBackDays must round-and-clamp into [floor_amt, ceiling_amt] every bar.
    eng = Engine(Cfg(floor_amt=20, ceiling_amt=60))
    rng = random.Random(2)
    p = 100.0
    for _ in range(400):
        p += rng.uniform(-3, 3)
        h = p + abs(rng.uniform(0, 2))
        l = p - abs(rng.uniform(0, 2))
        eng.update(p, h, l, p, 1.0)
        assert 20 <= eng.look_back_days <= 60
        assert eng.look_back_days == float(int(eng.look_back_days))  # integer-valued
