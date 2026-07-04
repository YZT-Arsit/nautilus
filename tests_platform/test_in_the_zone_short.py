"""Focused tests for the In The Zone short engine.

Pure Python; no Nautilus, no network. A down-move arms a short box (upper = prior
N-bar high, lower = bar-3 low); a close inside the box sets the trigger at that
bar's low, and a break below the trigger opens the short. The position covers on
an ATR profit target, an ATR protective stop, or (once the move has run in
favour) an ATR break-even stop. Runnable via ``pytest tests_platform -k
in_the_zone``.
"""
from __future__ import annotations

from strategies.in_the_zone_short.config import InTheZoneShortConfig as Cfg
from strategies.in_the_zone_short.engine import InTheZoneShortEngine as Engine


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


def _armed_short_base():
    # Warm up, then a down-move that arms the short box and sets the trigger at
    # the current bar's low (~97.1), then a bar whose low breaks the trigger.
    warm = [(100, 100.5, 99.5, 100)] * 7
    box = [(100, 100.3, 99, 99.5), (99.5, 99.6, 98, 98.2), (98.2, 98.4, 97, 97.2),
           (97.2, 99.8, 97.1, 99.2)]
    enter = [(99, 99.2, 97, 98)]      # Low 97 <= trigger 97.1 -> short
    return warm + box + enter


def test_entry_and_protect_stop():
    bars = _armed_short_base() + [(98, 101, 97.5, 100)]   # High rallies to the protective stop
    sigs = _signals(Engine(_cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_protect_stop") in sigs


def test_profit_target_exit():
    bars = _armed_short_base() + [(85, 85.5, 84, 84.5)]   # Open gaps below the profit target
    sigs = _signals(Engine(_cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_profit_target") in sigs


def test_break_even_stop_exit():
    # A deep profit run arms the break-even stop (small multiplier), then a rally
    # back to the entry price covers at break-even.
    bars = _armed_short_base() + [(97, 97.1, 92, 92.5), (93, 98, 92.8, 97.5)]
    sigs = _signals(Engine(_cfg(break_even_stop_atr_multi=1.0, profit_target_atr_multi=12.0)), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_breakeven_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _armed_short_base(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_uptrend():
    # A rising series never produces the Close[1] <= Low[3] down-move -> no zone,
    # no short.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(40)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
