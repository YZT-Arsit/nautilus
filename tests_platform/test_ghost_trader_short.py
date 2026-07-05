"""Focused tests for the Ghost Trader short engine.

Pure Python; no Nautilus, no network. A simulated short is tracked continuously
(fast EMA below slow EMA, RSI above the oversold level, a new low), exiting on a
break above the Donchian upper channel; a **real** short is only placed once the
most recent simulated trade closed at a loss. Runnable via ``pytest
tests_platform -k ghost_trader``.
"""
from __future__ import annotations

from strategies.ghost_trader_short.config import GhostTraderShortConfig as Cfg
from strategies.ghost_trader_short.engine import GhostTraderShortEngine as Engine


def _cfg():
    return Cfg(fast_length=3, slow_length=6, rsi_length=5, over_sold=30, over_bought=70, donchian_length=3)


def _build(moves, start=130.0):
    price = start
    bars = []
    prev_c = price
    for m in moves:
        o = prev_c
        c = prev_c + m
        h = max(o, c) + 0.6
        low = min(o, c) - 0.6
        bars.append((o, h, low, c))
        prev_c = c
    return bars


def _choppy_downtrend(n=20):
    # Net-down with a +3.5 bounce every 4th bar (forces a Donchian-upper exit)
    # while staying choppy enough to keep RSI above the oversold gate.
    return _build([3.5 if i % 4 == 3 else -1.4 for i in range(n)])


def _events(eng, bars, volume=1.0):
    out = []
    for o, h, low, c in bars:
        sig, reason = eng.update(o, h, low, c, volume)
        if sig != "HOLD":
            out.append((eng._bar, sig, reason))
    return out


def test_real_short_and_cover_after_sim_loss():
    ev = _events(Engine(_cfg()), _choppy_downtrend(20))
    reasons = [r for _, _, r in ev]
    assert "enter_short" in reasons
    assert "exit_cover" in reasons


def test_no_real_order_before_first_sim_loss():
    # The first real SELL must come only after a simulated trade has closed at a
    # loss (myProfit < 0). The engine tracks the simulated position continuously;
    # the very first simulated entry cannot place a real order.
    eng = Engine(_cfg())
    first_real_bar = None
    saw_sim_loss = False
    for o, h, low, c in _choppy_downtrend(20):
        sig, _ = eng.update(o, h, low, c, 1.0)
        if eng.my_profit < 0:
            saw_sim_loss = True
        if sig == "SELL" and first_real_bar is None:
            first_real_bar = eng._bar
            # A real short only fires once a simulated loss has been booked.
            assert saw_sim_loss
    assert first_real_bar is not None


def test_no_short_in_pure_uptrend():
    # Fast EMA stays above slow EMA -> the short setup never triggers -> no real
    # short (and no simulated short entry either).
    eng = Engine(_cfg())
    ev = _events(eng, _build([1.0] * 40))
    assert all(s != "SELL" for _, s, _ in ev)
    assert eng.position == 0
