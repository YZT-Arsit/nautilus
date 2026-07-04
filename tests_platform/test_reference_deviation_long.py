"""Focused tests for the Reference Deviation System long engine (mirror of short).

Pure Python; no Nautilus, no network. RDV is a -100..100 mean-deviation
oscillator; a sustained advance (price persistently above the MA) drives RDV
strongly positive -> long, and a decline back below the MA pushes RDV below zero
-> sell. Runnable via ``pytest tests_platform -k reference_deviation``.
"""
from __future__ import annotations

from strategies.reference_deviation_long.config import ReferenceDeviationLongConfig as Cfg
from strategies.reference_deviation_long.engine import ReferenceDeviationLongEngine as Engine


def _bar(p):
    return (p, p, p, p)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _advance_then_decline():
    warm = [_bar(100) for _ in range(20)]
    up = [_bar(100 + i * 1.0) for i in range(1, 21)]    # RDV -> strongly positive
    down = [_bar(120 - i * 2.0) for i in range(1, 25)]  # decline -> RDV back below 0
    return warm + up + down


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg()), _advance_then_decline())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_sell") in sigs


def test_entry_requires_volume():
    eng = Engine(Cfg())
    sigs = _signals(eng, _advance_then_decline(), volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_long_in_downtrend():
    # A steady downtrend keeps price below the MA -> RDV negative -> never longs.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(200 - i) for i in range(60)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
