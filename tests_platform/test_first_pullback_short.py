"""Focused tests for the First-PullBack short engine.

Pure Python; no Nautilus, no network. The MACD signal line crossing below zero
flags a downtrend that arms a Close/ATR channel; a lower-band break opens a short,
covered when the trend ends (``exit_uptrend``) or price rallies through the
trend-high (``exit_trend_high``) / exit (``exit_band``) levels. Runnable via
``pytest tests_platform -k first_pullback``.
"""
from __future__ import annotations

from strategies.first_pullback_short.config import FirstPullbackShortConfig as Cfg
from strategies.first_pullback_short.engine import FirstPullbackShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _uptrend_then_downtrend(tail):
    """40 rising bars (signal line above zero) then 40 falling bars (crosses under
    zero -> downtrend arms the channel, lower lows break the lower band), plus a
    caller-supplied tail that provokes a particular exit."""
    bars = []
    p = 100.0
    for _ in range(40):
        p += 1.0
        bars.append((p - 0.3, p + 0.4, p - 0.3, p))
    for _ in range(40):
        p -= 1.0
        bars.append((p + 0.3, p + 0.3, p - 0.6, p))
    return bars + tail(p)


def test_entry_and_exit_uptrend():
    # After the short, a sustained rally recrosses the signal line above zero ->
    # UpTrend[1] -> cover at Open.
    def tail(p):
        out = []
        for _ in range(30):
            p += 1.2
            out.append((p - 0.3, p + 0.5, p - 0.3, p))
        return out

    sigs = _signals(Engine(Cfg()), _uptrend_then_downtrend(tail))
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_uptrend") in sigs


def test_entry_and_exit_trend_high():
    # A single tall high spike (below a still-negative signal line) pierces the
    # trend-high line while it sits at/under the exit band -> exit_trend_high.
    def tail(p):
        out = [(p + 0.2, p + 30.0, p - 0.2, p - 0.3)]
        p -= 0.3
        for _ in range(6):
            p -= 0.5
            out.append((p + 0.2, p + 0.3, p - 0.4, p))
        return out

    sigs = _signals(Engine(Cfg()), _uptrend_then_downtrend(tail))
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_trend_high") in sigs


def test_entry_and_exit_band():
    # With exit_atr_pcnt=0 the exit band collapses to Close[1]; an early wick lifts
    # the trend high above it so the trend-high clause is skipped and a large spike
    # trips the exit band directly.
    bars = []
    p = 100.0
    for _ in range(40):
        p += 1.0
        bars.append((p - 0.3, p + 0.4, p - 0.3, p))
    for k in range(40):
        p -= 1.0
        h = p + 0.3 + (8.0 if k == 12 else 0.0)
        bars.append((p + 0.3, h, p - 0.6, p))
    bars.append((p + 0.2, p + 60.0, p - 0.2, p - 0.3))
    p -= 0.3
    for _ in range(4):
        p -= 0.5
        bars.append((p + 0.2, p + 0.3, p - 0.4, p))

    sigs = _signals(Engine(Cfg(exit_atr_pcnt=0.0)), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_band") in sigs


def test_no_entry_in_pure_uptrend():
    # A relentless rise never crosses the signal line under zero -> no downtrend,
    # no short.
    eng = Engine(Cfg())
    sigs = _signals(eng, [(100 + i, 100.4 + i, 99.7 + i, 100 + i) for i in range(80)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
