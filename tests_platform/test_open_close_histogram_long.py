"""Focused tests for the Open/Close Histogram long engine (mirror of short).

Pure Python; no Nautilus, no network. Bearish bars (close<open) push the
EMA(Close)-EMA(Open) histogram negative, bullish bars (close>open) push it
positive; a bearish->bullish flip crosses the histogram over zero (arming the
ATR-offset triggers), a high break opens the long, and the two exits (reverse
down-cross, exit-trigger low break) are isolated by what follows. Runnable via
``pytest tests_platform -k open_close_histogram``.
"""
from __future__ import annotations

from strategies.open_close_histogram_long.config import OpenCloseHistogramLongConfig as Cfg
from strategies.open_close_histogram_long.engine import OpenCloseHistogramLongEngine as Engine


def _bull(p):
    return (p, p + 1.0, p - 0.3, p + 0.8)  # close > open -> histogram up


def _bear(p):
    return (p, p + 0.3, p - 1.0, p - 0.8)  # close < open -> histogram down


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _bears():
    return [_bear(103 - i * 0.2) for i in range(14)]


def _bulls():
    return [_bull(100 + i * 0.6) for i in range(14)]


def test_entry_then_exit_trigger():
    # After the long a low plunge clears LongExitPrice (no trend flip first).
    bars = _bears() + _bulls() + [_bull(108)] + [(108, 108.5, 92, 93)] + [_bull(93) for _ in range(3)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_trigger") in sigs


def test_entry_then_exit_trend_down():
    # A gentle bearish move flips the histogram back below zero -> reverse sell.
    bars = _bears() + _bulls() + [_bear(108 - i * 0.8) for i in range(1, 16)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_trend_down") in sigs


def test_entry_requires_volume():
    bars = _bears() + _bulls() + [_bull(108)] + [(108, 108.5, 92, 93)] + [_bull(93) for _ in range(3)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_long_when_all_bearish():
    # Purely bearish bars keep the histogram negative -> no up-cross -> no long.
    eng = Engine(Cfg())
    sigs = _signals(eng, _bears() * 4)
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
