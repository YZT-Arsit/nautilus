"""Focused tests for the Escalator long engine.

Pure Python; no Nautilus, no network. A bullish dual-MA regime plus a two-bar
close-position pattern (near-low then near-high) buys a break of the 2-bar high
channel; the position exits on a risk-multiple profit target or a recent-low stop.
Runnable via ``pytest tests_platform -k escalator``.
"""
from __future__ import annotations

from strategies.escalator_long.config import EscalatorLongConfig as Cfg
from strategies.escalator_long.engine import EscalatorLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _base():
    """50 up bars (price above both MAs) + a near-low bar + a near-high bar +
    a break of the 2-bar high -> long. Deterministic: entry=150.81, risk=149.49."""
    bars = []
    p = 100.0
    for _ in range(50):
        p += 1.0
        bars.append((p - 0.2, p + 0.5, p - 0.3, p + 0.2))
    bars.append((p + 0.2, p + 0.5, p - 0.5, p - 0.4))   # Condition1 (close near low)
    bars.append((p - 0.2, p + 0.8, p - 0.3, p + 0.7))   # Condition2 (close near high)
    p += 0.7
    bars.append((p - 0.1, p + 0.35, p - 0.2, p + 0.1))  # break the 2-bar high -> long
    return bars


def test_entry_and_exit_profit_target():
    # After the long, price keeps rising and pierces the risk-multiple target.
    tail = []
    p = 152.0
    for _ in range(10):
        p += 2.0
        tail.append((p - 0.2, p + 0.5, p - 0.3, p))
    sigs = _signals(Engine(Cfg()), _base() + tail)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_profit_target") in sigs


def test_entry_and_exit_stop():
    # A dip whose high stays below the target but whose low breaks the stop
    # (LongRisk) -> exit_stop (target has priority, so the high must stay under it).
    tail = [(150.8, 151.0, 148.0, 149.0)]   # high 151.0 < target 153.45, low <= 149.49
    sigs = _signals(Engine(Cfg()), _base() + tail)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(Cfg())
    tail = [(152.0, 155.0, 151.5, 154.5)]
    sigs = _signals(eng, _base() + tail, volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_downtrend():
    # A relentless fall keeps price below both MAs -> bullish regime never holds.
    eng = Engine(Cfg())
    sigs = _signals(eng, [(160 - i, 160.4 - i, 159.5 - i, 159.6 - i) for i in range(80)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
