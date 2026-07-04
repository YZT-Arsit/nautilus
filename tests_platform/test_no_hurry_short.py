"""Focused tests for the No Hurry short engine.

Pure Python; no Nautilus, no network. The system reads a high/low channel shifted
back ``chan_delay + 1`` bars: a fresh break of the shifted *lower* channel opens a
short, and a rally through either the ATR trailing stop (``PosLow[1] +
ATRVal[1]``) or the shifted *upper* channel covers. Runnable via ``pytest
tests_platform -k no_hurry``.
"""
from __future__ import annotations

from strategies.no_hurry_short.config import NoHurryShortConfig as Cfg
from strategies.no_hurry_short.engine import NoHurryShortEngine as Engine


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


def _breakdown_then_bounce():
    # Flat, gently rising plateau (sets the shifted channel), then a sharp drop
    # that breaks the shifted lower channel (short), then a strong rally that
    # blows through the trailing stop (cover).
    plateau = [(100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100 + i * 0.1) for i in range(12)]
    drop = [(95, 95.2, 90, 90.5), (90, 90.2, 85, 85.5)]          # break shifted lower channel
    hold = [(85, 85.5, 84.5, 85)]                                 # PosLow[1]/ATRVal[1] settle
    rally = [(86, 120, 85.5, 119)]                                # High blows through the stop
    return plateau + drop + hold + rally


def test_entry_and_exit():
    sigs = _signals(Engine(_fast_cfg()), _breakdown_then_bounce())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_stop") in sigs


def test_no_entry_without_breakdown():
    # A quietly rising series never breaks the shifted lower channel -> no short.
    eng = Engine(_fast_cfg())
    bars = [(100 + i * 0.5, 101 + i * 0.5, 99.5 + i * 0.5, 100.5 + i * 0.5) for i in range(40)]
    sigs = _signals(eng, bars)
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    # Exit is gated by bars_since_entry > 0: entry and cover cannot both fire on
    # one bar, so a short must open before it can be covered.
    eng = Engine(_fast_cfg())
    sigs = _signals(eng, _breakdown_then_bounce())
    kinds = [reason for _, reason in sigs]
    assert kinds.index("enter_short") < kinds.index("exit_stop")
