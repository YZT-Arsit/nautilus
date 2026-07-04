"""Focused tests for the In The Zone long engine (mirror of short).

Pure Python; no Nautilus, no network. An up-move arms a long box (upper = bar-3
high, lower = prior N-bar low); a close inside the box sets the trigger at that
bar's high, and a break above the trigger opens the long. The position sells on an
ATR profit target, an ATR protective stop, or (once the move has run in favour)
an ATR break-even stop. Runnable via ``pytest tests_platform -k in_the_zone``.
"""
from __future__ import annotations

from strategies.in_the_zone_long.config import InTheZoneLongConfig as Cfg
from strategies.in_the_zone_long.engine import InTheZoneLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _cfg(**kw):
    base = dict(atr_length=5, cancel_flag_n=3, protect_stop_atr_multi=0.5,
               break_even_stop_atr_multi=3.0, profit_target_atr_multi=5.0)
    base.update(kw)
    return Cfg(**base)


def _armed_long_base():
    # Warm up, then an up-move that arms the long box; the pullback close sits
    # inside [DownLine, UpLine=High[3]] and sets the trigger at the bar's high
    # (~102.9), then a bar whose high breaks the trigger.
    warm = [(100, 100.5, 99.5, 100)] * 7
    box = [(100, 101, 99.7, 100.8), (100.8, 102, 100.6, 101.8), (101.8, 103, 101.6, 102.8),
           (102.8, 102.9, 100.5, 101.0)]
    enter = [(101, 103.5, 101, 103)]     # High 103.5 >= trigger 102.9 -> long
    return warm + box + enter


def test_entry_and_protect_stop():
    bars = _armed_long_base() + [(103, 103.2, 98, 98.5)]   # Low drops to the protective stop
    sigs = _signals(Engine(_cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_protect_stop") in sigs


def test_profit_target_exit():
    bars = _armed_long_base() + [(130, 130.5, 129, 129.5)]  # Open gaps above the profit target
    sigs = _signals(Engine(_cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_profit_target") in sigs


def test_break_even_stop_exit():
    # A deep profit run arms the break-even stop (small multiplier), then a dip
    # back to the entry price sells at break-even.
    bars = _armed_long_base() + [(103, 110, 102.9, 109.5), (109, 109.2, 102, 103)]
    sigs = _signals(Engine(_cfg(break_even_stop_atr_multi=1.0, profit_target_atr_multi=12.0)), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_breakeven_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _armed_long_base(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_downtrend():
    # A falling series never produces the Close[1] >= High[3] up-move -> no zone,
    # no long.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 - i, 100.3 - i, 99.7 - i, 100 - i) for i in range(40)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
