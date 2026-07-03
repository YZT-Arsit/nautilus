"""Focused tests for the Trading Range Breakout long engine (mirror of short).

Pure Python; no Nautilus, no network. Crafted OHLCV paths exercise the quiet-range
gap-sum setup, the upside-breakout entry, and each of the three exits (initial
stop, ATR trailing stop, bearish-reversal). Runnable via
``pytest tests_platform -k trading_range``.
"""
from __future__ import annotations

from strategies.trading_range_breakout_long.config import TradingRangeBreakoutLongConfig as Cfg
from strategies.trading_range_breakout_long.engine import TradingRangeBreakoutLongEngine as Engine


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _setup_spike():
    """A quiet range with a recent low spike (keeps the initial stop low), then an
    upside-breakout setup bar that arms Condition1/2/3."""
    b = [(100, 100.2, 99.8, 100.0)]
    for _ in range(4):
        b += [(100, 100.2, 99.8, 100.0)]
    b += [(100, 100, 95, 100.0)]         # recent low spike -> range bottom
    b += [(100, 102.0, 100, 100.5)]      # range top
    b += [(101.0, 104.0, 100.8, 103.5)]  # breakout-up setup bar
    return b


# -- entry + initial stop ---------------------------------------------------

def test_entry_then_initial_stop():
    bars = [(100, 100, 98, 99.5), (100, 102, 100, 100.5)]
    for _ in range(5):
        bars += [(100, 100.2, 99.8, 100.0)]
    bars += [(101, 104, 100.8, 103.5)]                 # breakout-up setup
    bars += [(104, 105, 103.8, 104.8)]                 # entry bar
    bars += [(105, 106, 104.9, 105.8), (106, 107, 105.9, 106.8)]
    bars += [(107, 107, 90, 91)]                       # crash -> initial stop
    bars += [(91, 91.2, 90.5, 91)] * 3
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_initial_stop") in sigs


def test_entry_requires_volume():
    bars = _setup_spike() + [(104, 105, 103.8, 104.8)] + [(105, 106, 104.9, 105.8)] * 3
    eng = Engine(Cfg())
    sigs = [eng.update(o, h, l, c, 0.0)[0] for o, h, l, c in bars]  # Vol == 0
    assert all(s == "HOLD" for s in sigs)
    assert eng.position == 0


def test_no_entry_in_trending_market():
    # a steady downtrend has no quiet range -> Condition1 false -> no long.
    bars = [(100 - i, 100 - i + 0.5, 100 - i - 1, 100 - i - 0.8) for i in range(40)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars)
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


# -- trailing stop ----------------------------------------------------------

def test_trailing_stop_exit():
    bars = _setup_spike()
    bars += [(104, 104.1, 103.9, 104.0)]               # entry
    for p in [105, 105.4, 105.7, 105.9, 106.0]:        # gentle rise (small ATR, high peak)
        bars += [(p - 0.05, p + 0.05, p - 0.1, p)]
    bars += [(106, 106, 101, 101.5)]                   # drop > RangeL -> trailing stop
    bars += [(101, 101.2, 100.5, 101)] * 3
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_trailing_stop") in sigs


# -- bearish-reversal exit --------------------------------------------------

def test_reversal_exit():
    # Isolate the reversal exit by pushing the trailing threshold out of the way
    # (atr_s huge); a sustained flat-high then a drop to a new 7-bar low covers.
    bars = _setup_spike()
    bars += [(104, 104.1, 103.9, 104.0)]               # entry
    for _ in range(8):
        bars += [(108, 108.1, 107.9, 108.0)]           # flat-high: prior-7 lows all 107.9
    bars += [(108, 108, 107.4, 107.4)]                 # drop: close < RangeL, mid < prev low
    bars += [(107.4, 107.5, 107, 107.4)] * 3
    sigs = _signals(Engine(Cfg(atr_s=1000.0)), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_reversal") in sigs
