"""Focused tests for the No Hurry long engine (mirror of short).

Pure Python; no Nautilus, no network. The system reads a high/low channel shifted
back ``chan_delay + 1`` bars: a fresh break of the shifted *upper* channel opens a
long, and a decline through either the ATR trailing stop (``PosHigh[1] -
ATRVal[1]``) or the shifted *lower* channel flattens it. Runnable via ``pytest
tests_platform -k no_hurry``.
"""
from __future__ import annotations

from strategies.no_hurry_long.config import NoHurryLongConfig as Cfg
from strategies.no_hurry_long.engine import NoHurryLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _fast_cfg():
    # Small windows keep the deterministic path short.
    return Cfg(chan_length=5, chan_delay=3, trailing_atrs=2.0, atr_length=5, tick=0.01)


def _breakout_then_drop():
    # Flat, gently falling plateau (sets the shifted channel), then a sharp rally
    # that breaks the shifted upper channel (long), then a strong drop that blows
    # through the trailing stop (sell).
    plateau = [(100 - i * 0.1, 100.5 - i * 0.1, 99.5 - i * 0.1, 100 - i * 0.1) for i in range(12)]
    up = [(105, 110, 104.8, 109.5), (110, 115, 109.8, 114.5)]     # break shifted upper channel
    hold = [(115, 115.5, 114.5, 115)]                              # PosHigh[1]/ATRVal[1] settle
    drop = [(114, 114.5, 80, 81)]                                  # Low blows through the stop
    return plateau + up + hold + drop


def test_entry_and_exit():
    sigs = _signals(Engine(_fast_cfg()), _breakout_then_drop())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_stop") in sigs


def test_no_entry_without_breakout():
    # A quietly falling series never breaks the shifted upper channel -> no long.
    eng = Engine(_fast_cfg())
    bars = [(100 - i * 0.5, 100.5 - i * 0.5, 99 - i * 0.5, 99.5 - i * 0.5) for i in range(40)]
    sigs = _signals(eng, bars)
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    # Exit is gated by bars_since_entry > 0: entry and sell cannot both fire on
    # one bar, so a long must open before it can be flattened.
    eng = Engine(_fast_cfg())
    sigs = _signals(eng, _breakout_then_drop())
    kinds = [reason for _, reason in sigs]
    assert kinds.index("enter_long") < kinds.index("exit_stop")
