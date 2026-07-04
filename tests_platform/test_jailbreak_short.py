"""Focused tests for the JailBreak short engine.

Pure Python; no Nautilus, no network. A break below the prior long-period low
channel opens a short; the position covers on an ATR protective stop (when it is
tighter than the short-period high channel) or on a break above that high channel.
Runnable via ``pytest tests_platform -k jailbreak``.
"""
from __future__ import annotations

from strategies.jailbreak_short.config import JailBreakShortConfig as Cfg
from strategies.jailbreak_short.engine import JailBreakShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _channel_cfg():
    return Cfg(length1=8, length2=4, ips=4.0, atr_val=5, tick=0.01)


def _protect_cfg():
    # A small IPS makes the protective stop tighter than the high channel.
    return Cfg(length1=8, length2=4, ips=0.5, atr_val=5, tick=0.01)


def _breakdown_then_bounce():
    warm = [(100, 100.3, 99.7, 100)] * 10
    drop = [(99.7, 99.8, 97, 97.5), (97, 97.2, 95, 95.5)]   # break the prior 8-bar low -> short
    bounce = [(95.5, 101, 95.3, 100.5)]                     # High above the 4-bar high -> channel exit
    return warm + drop + bounce


def _sharp_drop_then_recover():
    warm = [(100, 100.3, 99.7, 100)] * 10
    drop = [(100, 100.2, 96, 96.5)]        # sharp drop: fill at the low channel, entry near the highs
    recover = [(96.5, 100, 96, 99.8)]      # High hits the (tight) protective stop
    return warm + drop + recover


def test_entry_and_channel_exit():
    sigs = _signals(Engine(_channel_cfg()), _breakdown_then_bounce())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_channel") in sigs


def test_protect_stop_exit():
    sigs = _signals(Engine(_protect_cfg()), _sharp_drop_then_recover())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_protect_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_channel_cfg())
    sigs = _signals(eng, _breakdown_then_bounce(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_without_breakdown():
    # A rising series never breaks below the prior low channel -> no short.
    eng = Engine(_channel_cfg())
    sigs = _signals(eng, [(100 + i * 0.5, 101 + i * 0.5, 99.5 + i * 0.5, 100.5 + i * 0.5) for i in range(40)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
