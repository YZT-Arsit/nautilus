"""Focused tests for the Superman System long engine (mirror of short).

Pure Python; no Nautilus, no network. The entry needs a strongly-bullish strength
index (a sharp multi-bar advance), a momentum flip from down to up (a trough ~4
bars before entry), and an upside channel break — so the shared path falls to a
trough then rises sharply. Each of the three exits (profit target, channel stop,
reverse signal) is isolated by what happens after the long. Runnable via
``pytest tests_platform -k superman``.
"""
from __future__ import annotations

from strategies.superman_long.config import SupermanLongConfig as Cfg
from strategies.superman_long.engine import SupermanLongEngine as Engine


def _bar(p, up=0.3, dn=0.3):
    return (p, p + up, p - dn, p)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _fall_then_rise():
    """Fall to a trough (momentum down) then a sharp 5-bar advance (strength ->
    +100, momentum flips up, price breaks the channel high) -> long entry."""
    fall = [_bar(140 - i * 2.0) for i in range(10)]
    trough = 140 - 9 * 2.0
    rise = [_bar(trough + i * 3.0, up=0.8, dn=0.2) for i in range(1, 10)]
    return fall, rise, trough


# -- entry + each exit ------------------------------------------------------

def test_entry_then_profit_target():
    fall, rise, trough = _fall_then_rise()
    top = trough + 9 * 3.0
    bars = fall + rise + [_bar(top + i * 3.0, up=0.8, dn=0.2) for i in range(1, 12)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_profit_target") in sigs


def test_entry_then_stop_loss():
    fall, rise, trough = _fall_then_rise()
    top = trough + 9 * 3.0
    bars = fall + rise + [_bar(top - i * 4.0, up=0.2, dn=1.5) for i in range(1, 12)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_stop_loss") in sigs


def test_entry_then_reverse_signal():
    # A moderate decline breaks the recent (high) channel low with a bearish
    # strength/momentum flip, but stays above the entry-bar stop channel low.
    fall, rise, trough = _fall_then_rise()
    top = trough + 9 * 3.0
    bars = fall + rise + [_bar(top - i * 1.2, up=0.15, dn=0.5) for i in range(1, 16)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_reverse") in sigs


# -- guards -----------------------------------------------------------------

def test_entry_requires_volume():
    fall, rise, trough = _fall_then_rise()
    top = trough + 9 * 3.0
    bars = fall + rise + [_bar(top + i * 3.0, up=0.8, dn=0.2) for i in range(1, 12)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_long_in_downtrend():
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(200 - i) for i in range(60)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
