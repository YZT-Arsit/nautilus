"""Focused tests for the Bollinger Bandit long engine.

Pure Python; no Nautilus, no network. A Bollinger upper-band breakout gated by a
positive ROC filter buys; an adaptive-length exit MA (shrinking in-trade) sells
when it sits below the upper band and price breaks it. Uses tuned short periods so
the bands warm quickly. Runnable via ``pytest tests_platform -k bollinger_bandit``.
"""
from __future__ import annotations

from strategies.bollinger_bandit_long.config import BollingerBanditLongConfig as Cfg
from strategies.bollinger_bandit_long.engine import BollingerBanditLongEngine as Engine


def _cfg():
    return Cfg(bollinger_lengths=20, offset=1.25, roc_calc_length=10, liq_length=20, liq_floor=5)


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _range_rise_drop():
    bars = []
    base = 100.0
    for i in range(30):                    # wide oscillation -> band clears the noise
        p = base + (2.0 if i % 2 == 0 else -2.0)
        bars.append((p, p + 0.3, p - 0.3, p))
    p = base
    for _ in range(10):                    # decisive rise (roc>0) -> break upper band
        p += 3.0
        bars.append((p - 0.4, p + 0.6, p - 0.5, p))
    for _ in range(15):                    # selloff -> price breaks the exit MA
        p -= 2.5
        bars.append((p + 0.3, p + 0.3, p - 0.6, p))
    return bars


def test_entry_and_exit():
    sigs = _signals(Engine(_cfg()), _range_rise_drop())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_liq_ma") in sigs


def test_roc_filter_blocks_entry_without_momentum():
    # A pure downtrend never has rocCalc > 0, so no long even if a high pokes up.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(160 - i, 160.3 - i, 159.7 - i, 160 - i) for i in range(80)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_adaptive_liq_days_shrinks_in_trade_and_resets_flat():
    eng = Engine(_cfg())
    days_in_trade = []
    for o, h, l, c in _range_rise_drop():
        eng.update(o, h, l, c, 1.0)
        if eng.position == 1:
            days_in_trade.append(eng.liq_days)
    assert days_in_trade == sorted(days_in_trade, reverse=True)
    assert min(days_in_trade) >= 5           # floored at liq_floor
    assert eng.position == 0
    assert eng.liq_days == 20                 # reset to liq_length once flat


def test_no_trade_during_warmup():
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 + i, 100 + i + 3, 100 + i - 0.2, 100 + i) for i in range(10)])
    assert sigs == []
    assert eng.position == 0
