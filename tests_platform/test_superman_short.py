"""Focused tests for the Superman System short engine.

Pure Python; no Nautilus, no network. The entry needs a strongly-bearish strength
index (a sharp multi-bar decline), a momentum flip from up to down (a peak ~4
bars before entry), and a downside channel break — so the shared path rises to a
peak then declines sharply. Each of the three exits (profit target, channel stop,
reverse signal) is isolated by what happens after the short. Runnable via
``pytest tests_platform -k superman``.
"""
from __future__ import annotations

from strategies.superman_short.config import SupermanShortConfig as Cfg
from strategies.superman_short.engine import SupermanShortEngine as Engine


def _bar(p, up=0.3, dn=0.3):
    return (p, p + up, p - dn, p)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _rise_then_decline():
    """Rise to a peak (momentum up) then a sharp 5-bar decline (strength -> -100,
    momentum flips down, price breaks the channel low) -> short entry."""
    rise = [_bar(100 + i * 2.0) for i in range(10)]
    peak = 100 + 9 * 2.0
    decline = [_bar(peak - i * 3.0, up=0.2, dn=0.8) for i in range(1, 10)]
    return rise, decline, peak


# -- entry + each exit ------------------------------------------------------

def test_entry_then_profit_target():
    rise, decline, peak = _rise_then_decline()
    bot = peak - 9 * 3.0
    bars = rise + decline + [_bar(bot - i * 3.0, up=0.2, dn=0.8) for i in range(1, 12)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_profit_target") in sigs


def test_entry_then_stop_loss():
    rise, decline, peak = _rise_then_decline()
    bot = peak - 9 * 3.0
    bars = rise + decline + [_bar(bot + i * 4.0, up=1.5, dn=0.2) for i in range(1, 12)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_stop_loss") in sigs


def test_entry_then_reverse_signal():
    # A moderate recovery breaks the recent (low) channel high with a bullish
    # strength/momentum flip, but stays below the entry-bar stop channel high.
    rise, decline, peak = _rise_then_decline()
    bot = peak - 9 * 3.0
    bars = rise + decline + [_bar(bot + i * 1.2, up=0.5, dn=0.15) for i in range(1, 16)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_reverse") in sigs


# -- guards -----------------------------------------------------------------

def test_entry_requires_volume():
    rise, decline, peak = _rise_then_decline()
    bot = peak - 9 * 3.0
    bars = rise + decline + [_bar(bot - i * 3.0, up=0.2, dn=0.8) for i in range(1, 12)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_short_in_uptrend():
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(100 + i) for i in range(60)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
