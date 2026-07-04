"""Focused tests for the Going in Style short engine.

Pure Python; no Nautilus, no network. After the prior bar makes a new low, a
break below ``Close[1] - ATR[1]*Trigger`` opens the short; a parabolic-SAR
trailing stop (initialised above the entry bar, then accelerating toward the
profit-peak low) covers on a rally back through the prior stop. Runnable via
``pytest tests_platform -k going_in_style``.
"""
from __future__ import annotations

from strategies.going_in_style_short.config import GoingInStyleShortConfig as Cfg
from strategies.going_in_style_short.engine import GoingInStyleShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _cfg():
    return Cfg(length=5, trigger=0.5, acceleration=0.06, first_bar_multp=2.0)


def _newlow_break_then_rally():
    warm = [(100, 100.3, 99.7, 100)] * 6
    decline = [(99.8, 99.9, 99, 99.2), (99, 99.1, 98, 98.2), (98, 98.1, 96.5, 97)]  # new low then break -> short
    deeper = [(97, 97.1, 95, 95.3), (95, 95.2, 93.5, 94)]                            # profit run
    rally = [(94, 99, 93.8, 98.5)]                                                   # rally through the stop -> cover
    return warm + decline + deeper + rally


def test_entry_and_trailing_stop_exit():
    sigs = _signals(Engine(_cfg()), _newlow_break_then_rally())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_cfg())
    sigs = _signals(eng, _newlow_break_then_rally(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_uptrend():
    # A rising series never makes the new low that arms the entry -> no short.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(40)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    eng = Engine(_cfg())
    sigs = _signals(eng, _newlow_break_then_rally())
    kinds = [r for _, r in sigs]
    assert kinds.index("enter_short") < kinds.index("exit_stop")
