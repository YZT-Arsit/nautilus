"""Focused tests for the First-PullBack long engine.

Pure Python; no Nautilus, no network. The MACD signal line crossing above zero
flags an uptrend that arms a Close/ATR channel; an upper-band break opens a long,
sold when the trend ends (``exit_downtrend``) or price drops through the trend-low
(``exit_trend_low``) / exit (``exit_band``) levels. Runnable via ``pytest
tests_platform -k first_pullback``.
"""
from __future__ import annotations

from strategies.first_pullback_long.config import FirstPullbackLongConfig as Cfg
from strategies.first_pullback_long.engine import FirstPullbackLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _downtrend_then_uptrend(tail):
    """40 falling bars (signal line below zero) then 40 rising bars (crosses above
    zero -> uptrend arms the channel, higher highs break the upper band), plus a
    caller-supplied tail that provokes a particular exit."""
    bars = []
    p = 140.0
    for _ in range(40):
        p -= 1.0
        bars.append((p + 0.3, p + 0.3, p - 0.4, p))
    for _ in range(40):
        p += 1.0
        bars.append((p - 0.3, p + 0.6, p - 0.3, p))
    return bars + tail(p)


def test_entry_and_exit_downtrend():
    # After the long, a sustained fall recrosses the signal line below zero ->
    # DnTrend[1] -> sell at Open.
    def tail(p):
        out = []
        for _ in range(30):
            p -= 1.2
            out.append((p + 0.3, p + 0.3, p - 0.5, p))
        return out

    sigs = _signals(Engine(Cfg()), _downtrend_then_uptrend(tail))
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_downtrend") in sigs


def test_entry_and_exit_trend_low():
    # A single deep low spike (below a still-positive signal line) pierces the
    # trend-low line while it sits at/above the exit band -> exit_trend_low.
    def tail(p):
        out = [(p - 0.2, p + 0.2, p - 30.0, p + 0.3)]
        p += 0.3
        for _ in range(6):
            p += 0.5
            out.append((p - 0.2, p + 0.4, p - 0.3, p))
        return out

    sigs = _signals(Engine(Cfg()), _downtrend_then_uptrend(tail))
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_trend_low") in sigs


def test_entry_and_exit_band():
    # With exit_atr_pcnt=0 the exit band collapses to Close[1]; an early wick pushes
    # the trend low below it so the trend-low clause is skipped and a large downward
    # spike trips the exit band directly.
    bars = []
    p = 140.0
    for _ in range(40):
        p -= 1.0
        bars.append((p + 0.3, p + 0.4, p - 0.3, p))
    for k in range(40):
        p += 1.0
        lo = p - 0.3 - (8.0 if k == 12 else 0.0)
        bars.append((p - 0.3, p + 0.6, lo, p))
    bars.append((p - 0.2, p + 0.2, p - 60.0, p + 0.3))
    p += 0.3
    for _ in range(4):
        p += 0.5
        bars.append((p - 0.2, p + 0.4, p - 0.3, p))

    sigs = _signals(Engine(Cfg(exit_atr_pcnt=0.0)), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_band") in sigs


def test_no_entry_in_pure_downtrend():
    # A relentless fall never crosses the signal line above zero -> no uptrend,
    # no long.
    eng = Engine(Cfg())
    sigs = _signals(eng, [(160 - i, 160.3 - i, 159.6 - i, 160 - i) for i in range(80)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
