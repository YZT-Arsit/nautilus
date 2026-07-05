"""Focused tests for the Escalator short engine.

Pure Python; no Nautilus, no network. A bearish dual-MA regime plus a two-bar
close-position pattern (near-high then near-low) shorts a break of the 2-bar low
channel; the position exits on a risk-multiple profit target or a recent-high stop.
Runnable via ``pytest tests_platform -k escalator``.
"""
from __future__ import annotations

from strategies.escalator_short.config import EscalatorShortConfig as Cfg
from strategies.escalator_short.engine import EscalatorShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _base():
    """50 down bars (price under both MAs) + a near-high bar + a near-low bar +
    a break of the 2-bar low -> short. Deterministic: entry=149.19, risk=150.51."""
    bars = []
    p = 200.0
    for _ in range(50):
        p -= 1.0
        bars.append((p + 0.2, p + 0.3, p - 0.5, p - 0.2))
    bars.append((p - 0.2, p + 0.5, p - 0.5, p + 0.4))   # Condition1 (close near high)
    bars.append((p + 0.2, p + 0.3, p - 0.8, p - 0.7))   # Condition2 (close near low)
    p -= 0.7
    bars.append((p + 0.1, p + 0.2, p - 0.35, p - 0.1))  # break the 2-bar low -> short
    return bars


def test_entry_and_exit_profit_target():
    # After the short, price keeps falling and pierces the risk-multiple target.
    tail = []
    p = 148.0
    for _ in range(10):
        p -= 2.0
        tail.append((p + 0.2, p + 0.3, p - 0.5, p))
    sigs = _signals(Engine(Cfg()), _base() + tail)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_profit_target") in sigs


def test_entry_and_exit_stop():
    # A rally whose low stays above the target but whose high tops the stop
    # (ShortRisk) -> exit_stop (target has priority, so the low must clear it).
    tail = [(149.2, 152.0, 147.0, 151.0)]   # low 147.0 > target 146.55, high >= 150.51
    sigs = _signals(Engine(Cfg()), _base() + tail)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(Cfg())
    tail = [(148.0, 148.2, 145.0, 145.5)]
    sigs = _signals(eng, _base() + tail, volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_uptrend():
    # A relentless rise keeps price above both MAs -> bearish regime never holds.
    eng = Engine(Cfg())
    sigs = _signals(eng, [(100 + i, 100.5 + i, 99.6 + i, 100.4 + i) for i in range(80)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
