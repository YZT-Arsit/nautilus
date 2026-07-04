"""Focused tests for the Open/Close Histogram short engine.

Pure Python; no Nautilus, no network. Bullish bars (close>open) push the
EMA(Close)-EMA(Open) histogram positive, bearish bars (close<open) push it
negative; a bullish->bearish flip crosses the histogram under zero (arming the
ATR-offset triggers), a low break opens the short, and the two exits (reverse
up-cross, exit-trigger high break) are isolated by what follows. Runnable via
``pytest tests_platform -k open_close_histogram``.
"""
from __future__ import annotations

from strategies.open_close_histogram_short.config import OpenCloseHistogramShortConfig as Cfg
from strategies.open_close_histogram_short.engine import OpenCloseHistogramShortEngine as Engine


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


def _bulls():
    return [_bull(100 + i * 0.2) for i in range(14)]


def _bears():
    return [_bear(103 - i * 0.6) for i in range(14)]


def test_entry_then_exit_trigger():
    # After the short a high spike clears ShortExitPrice (no trend flip first).
    bars = _bulls() + _bears() + [_bear(96)] + [(96, 112, 95.5, 95)] + [_bear(95) for _ in range(3)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_trigger") in sigs


def test_entry_then_exit_trend_up():
    # A gentle bullish recovery flips the histogram back above zero -> reverse cover.
    bars = _bulls() + _bears() + [_bull(95 + i * 0.8) for i in range(1, 16)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_trend_up") in sigs


def test_entry_requires_volume():
    bars = _bulls() + _bears() + [_bear(96)] + [(96, 112, 95.5, 95)] + [_bear(95) for _ in range(3)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_short_when_all_bullish():
    # Purely bullish bars keep the histogram positive -> no down-cross -> no short.
    eng = Engine(Cfg())
    sigs = _signals(eng, _bulls() * 4)
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
