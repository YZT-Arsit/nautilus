"""Focused tests for the RedRover long engine (mirror of short).

Pure Python; no Nautilus, no network. A flat warmup builds the ATR and the
support/resistance lines; a bar whose high breaks the prior resistance opens the
long, and the two exits (ATR profit target on a continued rise, reverse break
below the prior support on a drop) are isolated by what follows. Runnable via
``pytest tests_platform -k redrover``.
"""
from __future__ import annotations

from strategies.redrover_long.config import RedRoverLongConfig as Cfg
from strategies.redrover_long.engine import RedRoverLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _warm():
    # 12 flat bars: ATR(10) ready, support == 99.4, resistance == 100.6.
    return [(100, 100.6, 99.4, 100) for _ in range(12)]


def test_entry_then_take_profit():
    bars = _warm() + [(100, 103.0, 99.8, 102.5)]            # break resistance -> long
    bars += [(102.5, 110, 102.3, 109.5)] + [(109, 110.2, 109.5, 110)] * 3  # big rise -> ATR target
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_take_profit") in sigs


def test_entry_then_reverse():
    bars = _warm() + [(100, 103.0, 99.8, 102.5)]            # break resistance -> long
    bars += [(102.5, 102.7, 98.0, 98.5)] + [(98, 98.2, 97.5, 98)] * 3  # drop < support
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_reverse") in sigs


def test_entry_requires_volume():
    bars = _warm() + [(100, 103.0, 99.8, 102.5)]
    bars += [(102.5, 110, 102.3, 109.5)] + [(109, 110.2, 109.5, 110)] * 3
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_long_in_downtrend():
    # A steady downtrend never breaks above the prior resistance -> never longs.
    eng = Engine(Cfg())
    bars = [(200 - i, 200 - i + 0.4, 200 - i - 0.6, 200 - i - 0.5) for i in range(60)]
    sigs = _signals(eng, bars)
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
