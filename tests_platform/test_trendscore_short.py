"""Focused tests for the TrendScore short engine.

Pure Python; no Nautilus, no network. Drives :class:`TrendScoreShortEngine` with
crafted OHLCV paths to exercise the scoring, the entry gate (price & score below
their MAs), the ``[1]`` (previous-bar) semantics, and the ATR stop stack.
Runnable locally and on the server via ``pytest tests_platform -k trendscore``.
"""
from __future__ import annotations

from strategies.trendscore_short.config import TrendScoreShortConfig
from strategies.trendscore_short.engine import BUY, HOLD, SELL, TrendScoreShortEngine


def _bar(o, h, l, c, v=1.0):
    return (o, h, l, c, v)


def test_trend_score_counts_prior_closes():
    eng = TrendScoreShortEngine(TrendScoreShortConfig(look_back=3, ma_length=2, atr_length=2))
    # feed 3 rising closes; score is computed vs the prior closes in the buffer.
    eng.update(*_bar(10, 10, 10, 10))   # no priors -> score 0
    eng.update(*_bar(11, 11, 11, 11))   # priors [10] -> +1
    eng.update(*_bar(12, 12, 12, 12))   # priors [10,11] -> +2
    # A close below all 3 priors (10,11,12) -> -3.
    _, _ = eng.update(*_bar(9, 9, 9, 9))
    assert eng._prev_score == -3.0
    # A close above all recent priors -> +3.
    eng.update(*_bar(20, 20, 20, 20))
    assert eng._prev_score == 3.0


def test_no_entry_during_warmup():
    eng = TrendScoreShortEngine(TrendScoreShortConfig(look_back=3, ma_length=5, atr_length=3))
    # Fewer than ma_length bars -> MA[1] not ready -> never trades.
    sigs = [eng.update(*_bar(100 - i, 100 - i, 100 - i, 100 - i))[0] for i in range(4)]
    assert all(s == HOLD for s in sigs)
    assert eng.position == 0


def _downtrend_engine():
    return TrendScoreShortEngine(TrendScoreShortConfig(
        look_back=3, ma_length=4, atr_length=3,
        protect_stop_atr_multi=0.5, trail_stop_atr_multi=3.0, breakeven_stop_atr_multi=5.0,
    ))


def test_short_entry_when_price_and_score_below_mas():
    eng = _downtrend_engine()
    # Steady downtrend: each close is below the MA and the score is negative, so
    # once warmed the entry gate (Close[1]<=MA[1] and Score[1]<=ScoreMA[1]) fires.
    prices = [100, 99, 98, 97, 96, 95, 94, 93]
    signal = HOLD
    entry_i = None
    for i, p in enumerate(prices):
        signal, reason = eng.update(*_bar(p, p + 0.5, p - 1.0, p))
        if signal == SELL:
            entry_i = i
            break
    assert signal == SELL and entry_i is not None
    assert eng.position == -1
    assert eng.last_entry_price is not None       # booked at Open
    assert eng.protect_stop is not None           # High[1] + k*ATR[1]


def test_entry_requires_volume():
    eng = _downtrend_engine()
    prices = [100, 99, 98, 97, 96, 95, 94, 93]
    signals = [eng.update(*_bar(p, p + 0.5, p - 1.0, p, v=0.0))[0] for p in prices]
    assert all(s != SELL for s in signals)        # Vol == 0 blocks entry
    assert eng.position == 0


def test_protective_stop_covers_on_adverse_move():
    eng = _downtrend_engine()
    # Warm + enter short on a downtrend.
    for p in [100, 99, 98, 97, 96, 95]:
        sig, _ = eng.update(*_bar(p, p + 0.5, p - 1.0, p))
        if eng.position == -1:
            break
    assert eng.position == -1
    protect = eng.protect_stop
    assert protect is not None
    # Next bar: a sharp rally whose high blows through the protective stop -> cover.
    big = protect + 10.0
    sig, reason = eng.update(*_bar(protect, big, protect - 0.5, protect + 1.0))
    assert sig == BUY
    assert eng.position == 0
    assert "exit" in reason


def test_entry_bar_never_exits():
    # mp[1] must be -1 for the exit block to run, so the entry bar itself cannot
    # produce an exit even if its high is extreme.
    eng = _downtrend_engine()
    last = HOLD
    for p in [100, 99, 98, 97, 96, 95, 94]:
        # give each bar a huge high; entries still must not immediately exit.
        last, _ = eng.update(*_bar(p, p + 100.0, p - 1.0, p))
        if last == SELL:
            # on the entry bar the returned signal is SELL (open), never BUY.
            assert eng.position == -1
            break
    assert last == SELL
