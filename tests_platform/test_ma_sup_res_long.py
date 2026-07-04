"""Focused tests for the MA Support/Resistance long engine (mirror of short).

Pure Python; no Nautilus, no network. A golden-cross (close above MA) arms a
resistance line at the bar's high and tracks higher highs; a following
death-cross (close back below MA) records that resistance line as the long entry
line. A later close back above the entry line opens a long, exited by an ATR
protective / trailing stop. Runnable via ``pytest tests_platform -k ma_sup_res``.
"""
from __future__ import annotations

from strategies.ma_sup_res_long.config import MaSupResLongConfig as Cfg
from strategies.ma_sup_res_long.engine import MaSupResLongEngine as Engine


def _fast_cfg():
    return Cfg(ma_length=5, atr_length=5, protect_stop_atr_multi=0.5, trail_stop_atr_multi=2.5)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _pop_recover_break():
    # Seed MA, push below it, golden-cross up (arm resistance at higher highs),
    # death-cross back down (record long entry line ~105), then close back above
    # that line (long), then drop hard to hit the trailing stop (sell).
    warm = [(100, 100.5, 99.5, 100)] * 6
    down = [(100 - i, 100.5 - i, 99.5 - i, 100 - i) for i in range(1, 5)]
    up = [(96, 102, 95.8, 102), (102, 104, 101.5, 103.5), (103.5, 105, 103, 104.5)]
    fall = [(104, 104.2, 99, 99.5), (99, 99.5, 98.5, 99), (99, 99.5, 98.5, 99)]
    break_above = [(100, 106, 99.5, 106)]      # close 106 >= entry line 105
    entry_bar = [(105, 107, 104, 106)]         # long at open
    drop = [(106, 106.5, 90, 91)]              # Low blows through the trailing stop
    return warm + down + up + fall + break_above + entry_bar + drop


def test_entry_and_exit():
    sigs = _signals(Engine(_fast_cfg()), _pop_recover_break())
    assert ("BUY", "enter_long") in sigs
    assert any(s == "SELL" and r in ("exit_trail_stop", "exit_protect_stop") for s, r in sigs)


def test_no_entry_without_setup():
    # A quietly falling series never golden-crosses -> no entry line -> no long.
    eng = Engine(_fast_cfg())
    bars = [(100 - i * 0.5, 100.5 - i * 0.5, 99 - i * 0.5, 99.5 - i * 0.5) for i in range(40)]
    sigs = _signals(eng, bars)
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_no_trades_on_zero_volume():
    # Both entry and exit gate on Vol > 0.
    eng = Engine(_fast_cfg())
    sigs = _signals(eng, _pop_recover_break(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    eng = Engine(_fast_cfg())
    sigs = _signals(eng, _pop_recover_break())
    kinds = [r for _, r in sigs]
    assert kinds.index("enter_long") < min(
        kinds.index(k) for k in ("exit_trail_stop", "exit_protect_stop") if k in kinds
    )
