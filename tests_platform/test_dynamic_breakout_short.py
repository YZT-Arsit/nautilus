"""Focused tests for the Dynamic Breakout II short engine.

Pure Python; no Nautilus, no network. A volatility-adaptive Bollinger + Donchian
system: a bar closing under the lower band with a break of the Donchian lower
shorts; the position covers on a cross back above the adaptive mid-line (the liq
exit). Runnable via ``pytest tests_platform -k dynamic_breakout``.

Note on ``exit_reverse``: the reverse cover fires only if, while short, a prior
close tops the *upper* Bollinger band and price breaks the Donchian *upper*. Any
rally that far first crosses the mid-line, so the liq cover (checked with the same
``BarsSinceEntry >= 1`` availability but at the much nearer mid-line) always
preempts it. The branch is kept for source parity but is effectively unreachable
with the default levels, so it is not asserted here.
"""
from __future__ import annotations

import random

from strategies.dynamic_breakout_short.config import DynamicBreakoutShortConfig as Cfg
from strategies.dynamic_breakout_short.engine import DynamicBreakoutShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _quiet_then_drop_then_bounce():
    bars = []
    p = 100.0
    for i in range(70):                      # quiet range -> tight bands
        p += 0.3 if i % 2 == 0 else -0.3
        bars.append((p, p + 0.4, p - 0.4, p))
    for _ in range(10):                      # sharp drop -> break lower band + channel
        p -= 2.5
        bars.append((p + 0.5, p + 0.6, p - 0.6, p))
    for _ in range(20):                      # bounce back up to the mid-line
        p += 1.5
        bars.append((p - 0.3, p + 0.5, p - 0.4, p))
    return bars


def test_entry_and_exit_liq():
    sigs = _signals(Engine(Cfg()), _quiet_then_drop_then_bounce())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_liq") in sigs


def test_no_entry_in_pure_uptrend():
    # A relentless rise never closes below the lower band -> no short.
    eng = Engine(Cfg())
    sigs = _signals(eng, [(100 + i, 100.5 + i, 99.6 + i, 100 + i) for i in range(120)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_no_trades_on_zero_volume_still_runs():
    # No Vol > 0 gate in this system, so zero volume does not by itself block
    # trades; the engine must still process a full stream without raising.
    eng = Engine(Cfg())
    _signals(eng, _quiet_then_drop_then_bounce(), volume=0.0)
    assert eng.position in (0, -1)


def test_adaptive_lookback_stays_clamped():
    # lookBackDays must round-and-clamp into [floor_amt, ceiling_amt] every bar.
    eng = Engine(Cfg(floor_amt=20, ceiling_amt=60))
    rng = random.Random(1)
    p = 100.0
    for _ in range(400):
        p += rng.uniform(-3, 3)
        h = p + abs(rng.uniform(0, 2))
        l = p - abs(rng.uniform(0, 2))
        eng.update(p, h, l, p, 1.0)
        assert 20 <= eng.look_back_days <= 60
        assert eng.look_back_days == float(int(eng.look_back_days))  # integer-valued
