"""Focused tests for the Bollinger Bandit short engine.

Pure Python; no Nautilus, no network. A Bollinger lower-band breakout gated by a
negative ROC filter shorts; an adaptive-length exit MA (shrinking in-trade) covers
when it sits above the lower band and price breaks it. Uses tuned short periods so
the bands warm quickly. Runnable via ``pytest tests_platform -k bollinger_bandit``.
"""
from __future__ import annotations

from strategies.bollinger_bandit_short.config import BollingerBanditShortConfig as Cfg
from strategies.bollinger_bandit_short.engine import BollingerBanditShortEngine as Engine


def _cfg():
    return Cfg(bollinger_lengths=20, offset=1.25, roc_calc_length=10, liq_length=20, liq_floor=5)


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _range_drop_bounce():
    bars = []
    base = 200.0
    for i in range(30):                    # wide oscillation -> band clears the noise
        p = base + (2.0 if i % 2 == 0 else -2.0)
        bars.append((p, p + 0.3, p - 0.3, p))
    p = base
    for _ in range(10):                    # decisive decline (roc<0) -> break lower band
        p -= 3.0
        bars.append((p + 0.4, p + 0.5, p - 0.6, p))
    for _ in range(15):                    # strong bounce -> price breaks the exit MA
        p += 2.5
        bars.append((p - 0.3, p + 0.6, p - 0.3, p))
    return bars


def test_entry_and_exit():
    sigs = _signals(Engine(_cfg()), _range_drop_bounce())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_liq_ma") in sigs


def test_roc_filter_blocks_entry_without_momentum():
    # Force the ROC filter off (rocCalc[1] >= 0) by rising into the band break:
    # a pure uptrend never has rocCalc < 0, so no short even if a low dips.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(80)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_adaptive_liq_days_shrinks_in_trade_and_resets_flat():
    # While short the exit-MA period decrements each bar (floored); once flat it
    # resets to liq_length.
    eng = Engine(_cfg())
    bars = _range_drop_bounce()
    days_in_trade = []
    for o, h, l, c in bars:
        eng.update(o, h, l, c, 1.0)
        if eng.position == -1:
            days_in_trade.append(eng.liq_days)
    # the in-trade period strictly decreases (until the floor) then the engine
    # ends flat, having reset.
    assert days_in_trade == sorted(days_in_trade, reverse=True)
    assert min(days_in_trade) >= 5           # floored at liq_floor
    assert eng.position == 0
    assert eng.liq_days == 20                 # reset to liq_length once flat


def test_no_trade_during_warmup():
    # Fewer than bollinger_lengths bars -> no band -> no trade.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 - i, 100 - i + 0.2, 100 - i - 3, 100 - i) for i in range(10)])
    assert sigs == []
    assert eng.position == 0
