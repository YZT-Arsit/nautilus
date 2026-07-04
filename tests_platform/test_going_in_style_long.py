"""Focused tests for the Going in Style long engine (mirror of short).

Pure Python; no Nautilus, no network. After the prior bar makes a new high, a
break above ``Close[1] + ATR[1]*Trigger`` opens the long; a parabolic-SAR
trailing stop (initialised below the entry bar, then accelerating toward the
profit-peak high) sells on a drop back through the prior stop. Runnable via
``pytest tests_platform -k going_in_style``.
"""
from __future__ import annotations

from strategies.going_in_style_long.config import GoingInStyleLongConfig as Cfg
from strategies.going_in_style_long.engine import GoingInStyleLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _cfg(**kw):
    base = dict(length=5, trigger=0.79, acceleration=0.05, first_bar_multp=2.0)
    base.update(kw)
    return Cfg(**base)


def _newhigh_break_then_drop():
    warm = [(100, 100.3, 99.7, 100)] * 6
    rise = [(100.1, 101, 100, 100.8), (101, 102, 100.9, 101.8), (102, 104, 101.9, 103.5)]  # new high then break -> long
    run = [(103.5, 105, 103.4, 104.8), (105, 107, 104.9, 106.5), (107, 108, 106.9, 107.5)]  # profit run
    drop = [(107, 107.2, 100, 100.5)]                                                        # drop through the stop -> sell
    return warm + rise + run + drop


def test_entry_and_trailing_stop_exit():
    sigs = _signals(Engine(_cfg()), _newhigh_break_then_drop())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _newhigh_break_then_drop(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_downtrend():
    # A falling series never makes the new high that arms the entry -> no long.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 - i, 100.3 - i, 99.7 - i, 100 - i) for i in range(40)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    eng = Engine(_cfg())
    sigs = _signals(eng, _newhigh_break_then_drop())
    kinds = [r for _, r in sigs]
    assert kinds.index("enter_long") < kinds.index("exit_stop")
