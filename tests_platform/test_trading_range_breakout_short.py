"""Focused tests for the Trading Range Breakout short engine.

Pure Python; no Nautilus, no network. Crafted OHLCV paths exercise the quiet-range
gap-sum setup, the downside-breakout entry, and each of the three exits (initial
stop, ATR trailing stop, bullish-reversal). Runnable via
``pytest tests_platform -k trading_range``.
"""
from __future__ import annotations

from strategies.trading_range_breakout_short.config import TradingRangeBreakoutShortConfig as Cfg
from strategies.trading_range_breakout_short.engine import TradingRangeBreakoutShortEngine as Engine


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _setup_spike():
    """A quiet range with a recent high spike (keeps the initial stop high), then
    a downside-breakout setup bar that arms Condition1/2/4."""
    b = [(100, 100.2, 99.8, 100.0)]
    for _ in range(4):
        b += [(100, 100.2, 99.8, 100.0)]
    b += [(100, 105, 100, 100.0)]       # recent high spike -> range top
    b += [(100, 100.2, 98.0, 99.5)]     # range bottom
    b += [(99.0, 99.2, 96.0, 96.5)]     # breakout-down setup bar
    return b


# -- entry + initial stop ---------------------------------------------------

def test_entry_then_initial_stop():
    # wide range set by an early spike, breakout-down setup, then a rally that
    # blows through the initial stop (RangeH at entry).
    bars = [(100, 102, 100, 100.5), (100, 100, 98, 99.5)]
    for _ in range(5):
        bars += [(100, 100.2, 99.8, 100.0)]
    bars += [(99.0, 99.2, 96.0, 96.5)]                 # breakout-down setup
    bars += [(96.0, 96.2, 95.0, 95.2)]                 # entry bar
    bars += [(95.0, 95.1, 94.0, 94.2), (94.0, 94.1, 93.0, 93.2)]
    bars += [(93.0, 110.0, 93.0, 109.0)]               # rally -> initial stop
    bars += [(109, 109.2, 108.5, 109)] * 3
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_initial_stop") in sigs


def test_entry_requires_volume():
    bars = _setup_spike() + [(96.0, 96.2, 95.0, 95.2)] + [(95.0, 95.1, 94.0, 94.2)] * 3
    eng = Engine(Cfg())
    sigs = [eng.update(o, h, l, c, 0.0)[0] for o, h, l, c in bars]  # Vol == 0
    assert all(s == "HOLD" for s in sigs)
    assert eng.position == 0


def test_no_entry_in_trending_market():
    # a steady uptrend has no quiet range (small gap-sum) -> Condition1 false -> no short.
    bars = [(100 + i, 100 + i + 1, 100 + i - 0.5, 100 + i + 0.8) for i in range(40)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars)
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


# -- trailing stop ----------------------------------------------------------

def test_trailing_stop_exit():
    bars = _setup_spike()
    bars += [(96.0, 96.1, 95.5, 95.6)]                 # entry
    for p in [95.0, 94.6, 94.3, 94.1, 94.0]:           # gentle fall (small ATR, deep low)
        bars += [(p + 0.05, p + 0.1, p - 0.05, p)]
    bars += [(94.0, 99.0, 94.0, 98.5)]                 # rally < RangeH -> trailing stop
    bars += [(98, 98.2, 97.5, 98)] * 3
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_trailing_stop") in sigs


# -- bullish-reversal exit --------------------------------------------------

def test_reversal_exit():
    # Isolate the reversal exit by pushing the trailing threshold out of the way
    # (atr_s huge); a sustained flat-low then a pop to a new 7-bar high covers.
    bars = _setup_spike()
    bars += [(96.0, 96.1, 95.8, 96.0)]                 # entry
    for _ in range(8):
        bars += [(92.0, 92.1, 91.9, 92.0)]             # flat-low: prior-7 highs all 92.1
    bars += [(92.0, 92.6, 92.0, 92.6)]                 # pop: close 92.6 > RangeH, mid > prev high
    bars += [(92.6, 92.7, 92.2, 92.6)] * 3
    sigs = _signals(Engine(Cfg(atr_s=1000.0)), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_reversal") in sigs
