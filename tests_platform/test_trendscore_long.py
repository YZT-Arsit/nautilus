"""Focused tests for the TrendScore long engine (mirror of the short tests).

Pure Python; no Nautilus, no network. Drives :class:`TrendScoreLongEngine` with
crafted OHLCV paths to exercise scoring, the entry gate (price & score ABOVE
their MAs), the ``[1]`` semantics, and the ATR stop stack.
Runnable via ``pytest tests_platform -k trendscore``.
"""
from __future__ import annotations

from strategies.trendscore_long.config import TrendScoreLongConfig
from strategies.trendscore_long.engine import BUY, HOLD, SELL, TrendScoreLongEngine


def _bar(o, h, l, c, v=1.0):
    return (o, h, l, c, v)


def test_trend_score_counts_prior_closes():
    eng = TrendScoreLongEngine(TrendScoreLongConfig(look_back=3, ma_length=2, atr_length=2))
    eng.update(*_bar(10, 10, 10, 10))
    eng.update(*_bar(11, 11, 11, 11))
    eng.update(*_bar(12, 12, 12, 12))
    eng.update(*_bar(20, 20, 20, 20))   # above all recent priors -> +3
    assert eng._prev_score == 3.0
    eng.update(*_bar(5, 5, 5, 5))       # below all recent priors -> -3
    assert eng._prev_score == -3.0


def test_no_entry_during_warmup():
    eng = TrendScoreLongEngine(TrendScoreLongConfig(look_back=3, ma_length=5, atr_length=3))
    sigs = [eng.update(*_bar(100 + i, 100 + i, 100 + i, 100 + i))[0] for i in range(4)]
    assert all(s == HOLD for s in sigs)
    assert eng.position == 0


def _uptrend_engine():
    return TrendScoreLongEngine(TrendScoreLongConfig(
        look_back=3, ma_length=4, atr_length=3,
        protect_stop_atr_multi=0.5, trail_stop_atr_multi=3.0, breakeven_stop_atr_multi=5.0,
    ))


def test_long_entry_when_price_and_score_above_mas():
    eng = _uptrend_engine()
    prices = [100, 101, 102, 103, 104, 105, 106, 107]
    signal, entry_i = HOLD, None
    for i, p in enumerate(prices):
        signal, reason = eng.update(*_bar(p, p + 1.0, p - 0.5, p))
        if signal == BUY:
            entry_i = i
            break
    assert signal == BUY and entry_i is not None
    assert eng.position == 1
    assert eng.last_entry_price is not None        # booked at Open
    assert eng.protect_stop is not None            # Low[1] - k*ATR[1]


def test_entry_requires_volume():
    eng = _uptrend_engine()
    prices = [100, 101, 102, 103, 104, 105, 106, 107]
    signals = [eng.update(*_bar(p, p + 1.0, p - 0.5, p, v=0.0))[0] for p in prices]
    assert all(s != BUY for s in signals)
    assert eng.position == 0


def test_protective_stop_sells_on_adverse_move():
    eng = _uptrend_engine()
    for p in [100, 101, 102, 103, 104, 105]:
        eng.update(*_bar(p, p + 1.0, p - 0.5, p))
        if eng.position == 1:
            break
    assert eng.position == 1
    protect = eng.protect_stop
    assert protect is not None
    # A sharp drop whose low pierces the protective stop -> sell (flatten).
    deep = protect - 10.0
    sig, reason = eng.update(*_bar(protect, protect + 0.5, deep, protect - 1.0))
    assert sig == SELL
    assert eng.position == 0
    assert "exit" in reason


def test_entry_bar_never_exits():
    eng = _uptrend_engine()
    last = HOLD
    for p in [100, 101, 102, 103, 104, 105, 106]:
        last, _ = eng.update(*_bar(p, p + 1.0, p - 100.0, p))  # huge low each bar
        if last == BUY:
            assert eng.position == 1     # opened, not immediately stopped out
            break
    assert last == BUY
