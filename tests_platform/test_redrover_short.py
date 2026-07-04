"""Focused tests for the RedRover short engine.

Pure Python; no Nautilus, no network. A flat warmup builds the ATR and the
support/resistance lines; a bar whose low breaks the prior support opens the
short, and the two exits (ATR profit target on a continued drop, reverse break
above the prior resistance on a bounce) are isolated by what follows. Runnable
via ``pytest tests_platform -k redrover``.
"""
from __future__ import annotations

from strategies.redrover_short.config import RedRoverShortConfig as Cfg
from strategies.redrover_short.engine import RedRoverShortEngine as Engine


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
    bars = _warm() + [(100, 100.2, 97.0, 97.5)]              # break support -> short
    bars += [(97, 97.2, 90, 90.5)] + [(90, 90.2, 89.5, 90)] * 3  # big drop -> ATR target
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_take_profit") in sigs


def test_entry_then_reverse():
    bars = _warm() + [(100, 100.2, 97.0, 97.5)]              # break support -> short
    bars += [(97.5, 101.5, 97.3, 101.0)] + [(101, 101.2, 100.5, 101)] * 3  # bounce > resistance
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_reverse") in sigs


def test_entry_requires_volume():
    bars = _warm() + [(100, 100.2, 97.0, 97.5)]
    bars += [(97, 97.2, 90, 90.5)] + [(90, 90.2, 89.5, 90)] * 3
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_short_in_uptrend():
    # A steady uptrend never breaks below the prior support -> never shorts.
    eng = Engine(Cfg())
    bars = [(100 + i, 100 + i + 0.6, 100 + i - 0.4, 100 + i + 0.5) for i in range(60)]
    sigs = _signals(eng, bars)
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
