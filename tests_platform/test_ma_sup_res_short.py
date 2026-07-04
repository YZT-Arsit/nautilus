"""Focused tests for the MA Support/Resistance short engine.

Pure Python; no Nautilus, no network. A death-cross (close below MA) arms a
support line at the bar's low and tracks lower lows; a following golden-cross
(close back above MA) records that support line as the short entry line. A later
close back below the entry line opens a short, exited by an ATR protective /
trailing stop. Runnable via ``pytest tests_platform -k ma_sup_res``.
"""
from __future__ import annotations

from strategies.ma_sup_res_short.config import MaSupResShortConfig as Cfg
from strategies.ma_sup_res_short.engine import MaSupResShortEngine as Engine


def _fast_cfg():
    return Cfg(ma_length=5, atr_length=5, protect_stop_atr_multi=0.5, trail_stop_atr_multi=2.5)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _dip_recover_break():
    # Seed MA, push above it, death-cross down (arm support at lower lows),
    # golden-cross back up (record short entry line ~95), then close back below
    # that line (short), then rally hard to hit the trailing stop (cover).
    warm = [(100, 100.5, 99.5, 100)] * 6
    up = [(100 + i, 100.5 + i, 99.5 + i, 100 + i) for i in range(1, 5)]
    down = [(104, 104.2, 98, 98.2), (98, 98.3, 96, 96.5), (96, 96.5, 95, 95.5)]
    recover = [(95, 101, 94.8, 101), (101, 101.5, 100.5, 101), (101, 101.5, 100.5, 101)]
    break_below = [(100, 100.5, 94, 94.5)]     # close 94.5 <= entry line 95
    entry_bar = [(95, 96, 93, 94)]             # short at open
    rally = [(94, 110, 93.5, 109)]             # High blows through the trailing stop
    return warm + up + down + recover + break_below + entry_bar + rally


def test_entry_and_exit():
    sigs = _signals(Engine(_fast_cfg()), _dip_recover_break())
    assert ("SELL", "enter_short") in sigs
    assert any(s == "BUY" and r in ("exit_trail_stop", "exit_protect_stop") for s, r in sigs)


def test_no_entry_without_setup():
    # A quietly rising series never death-crosses -> no entry line -> no short.
    eng = Engine(_fast_cfg())
    bars = [(100 + i * 0.5, 101 + i * 0.5, 99.5 + i * 0.5, 100.5 + i * 0.5) for i in range(40)]
    sigs = _signals(eng, bars)
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_no_trades_on_zero_volume():
    # Both entry and exit gate on Vol > 0.
    eng = Engine(_fast_cfg())
    sigs = _signals(eng, _dip_recover_break(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    eng = Engine(_fast_cfg())
    sigs = _signals(eng, _dip_recover_break())
    kinds = [r for _, r in sigs]
    assert kinds.index("enter_short") < min(
        kinds.index(k) for k in ("exit_trail_stop", "exit_protect_stop") if k in kinds
    )
