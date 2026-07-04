"""Focused tests for the Reference Deviation System short engine.

Pure Python; no Nautilus, no network. RDV is a -100..100 mean-deviation
oscillator; a sustained decline (price persistently below the MA) drives RDV
strongly negative -> short, and a recovery back above the MA pushes RDV above
zero -> cover. Runnable via ``pytest tests_platform -k reference_deviation``.
"""
from __future__ import annotations

from strategies.reference_deviation_short.config import ReferenceDeviationShortConfig as Cfg
from strategies.reference_deviation_short.engine import ReferenceDeviationShortEngine as Engine


def _bar(p):
    return (p, p, p, p)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _decline_then_recover():
    warm = [_bar(100) for _ in range(20)]
    down = [_bar(100 - i * 1.0) for i in range(1, 21)]   # RDV -> strongly negative
    up = [_bar(80 + i * 2.0) for i in range(1, 25)]      # recovery -> RDV back above 0
    return warm + down + up


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg()), _decline_then_recover())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_cover") in sigs


def test_entry_requires_volume():
    eng = Engine(Cfg())
    sigs = _signals(eng, _decline_then_recover(), volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_short_in_uptrend():
    # A steady uptrend keeps price above the MA -> RDV positive -> never shorts.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(100 + i) for i in range(60)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
