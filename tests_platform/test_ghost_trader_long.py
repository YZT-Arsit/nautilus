"""Focused tests for the Ghost Trader long engine (mirror of short).

Pure Python; no Nautilus, no network. A simulated long is tracked continuously
(fast EMA above slow EMA, RSI below the overbought level, a new high), exiting on
a break below the Donchian lower channel; a **real** long is only placed once the
most recent simulated trade closed at a loss. Runnable via ``pytest
tests_platform -k ghost_trader``.
"""
from __future__ import annotations

from strategies.ghost_trader_long.config import GhostTraderLongConfig as Cfg
from strategies.ghost_trader_long.engine import GhostTraderLongEngine as Engine


def _cfg():
    return Cfg(fast_length=3, slow_length=6, rsi_length=5, over_sold=30, over_bought=70, donchian_length=3)


def _build(moves, start=100.0):
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


def _choppy_uptrend(n=24):
    # Net-up with a -3.5 dip every 4th bar (forces a Donchian-lower exit) while
    # staying choppy enough to keep RSI below the overbought gate.
    return _build([-3.5 if i % 4 == 3 else 1.4 for i in range(n)])


def _events(eng, bars, volume=1.0):
    out = []
    for o, h, low, c in bars:
        sig, reason = eng.update(o, h, low, c, volume)
        if sig != "HOLD":
            out.append((eng._bar, sig, reason))
    return out


def test_real_long_and_sell_after_sim_loss():
    ev = _events(Engine(_cfg()), _choppy_uptrend(24))
    reasons = [r for _, _, r in ev]
    assert "enter_long" in reasons
    assert "exit_sell" in reasons


def test_no_real_order_before_first_sim_loss():
    # The first real BUY must come only after a simulated trade has closed at a
    # loss (myProfit < 0). The very first simulated entry cannot place a real
    # order.
    eng = Engine(_cfg())
    first_real_bar = None
    saw_sim_loss = False
    for o, h, low, c in _choppy_uptrend(24):
        sig, _ = eng.update(o, h, low, c, 1.0)
        if eng.my_profit < 0:
            saw_sim_loss = True
        if sig == "BUY" and first_real_bar is None:
            first_real_bar = eng._bar
            assert saw_sim_loss
    assert first_real_bar is not None


def test_no_long_in_pure_downtrend():
    # Fast EMA stays below slow EMA -> the long setup never triggers -> no real
    # long (and no simulated long entry either).
    eng = Engine(_cfg())
    ev = _events(eng, _build([-1.0] * 40))
    assert all(s != "BUY" for _, s, _ in ev)
    assert eng.position == 0
