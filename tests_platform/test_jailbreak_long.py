"""Focused tests for the JailBreak long engine (mirror of short).

Pure Python; no Nautilus, no network. A break above the prior long-period high
channel opens a long; the position sells on an ATR protective stop (when it is
tighter than the short-period low channel) or on a break below that low channel.
Runnable via ``pytest tests_platform -k jailbreak``.
"""
from __future__ import annotations

from strategies.jailbreak_long.config import JailBreakLongConfig as Cfg
from strategies.jailbreak_long.engine import JailBreakLongEngine as Engine


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
    # A small IPS makes the protective stop tighter than the low channel.
    return Cfg(length1=8, length2=4, ips=0.5, atr_val=5, tick=0.01)


def _breakout_then_drop():
    warm = [(100, 100.3, 99.7, 100)] * 10
    up = [(100.3, 103, 100.2, 102.5), (103, 105, 102.8, 104.5)]   # break the prior 8-bar high -> long
    drop = [(104.5, 104.7, 99, 99.5)]                             # Low below the 4-bar low -> channel exit
    return warm + up + drop


def _sharp_up_then_dip():
    warm = [(100, 100.3, 99.7, 100)] * 10
    up = [(100, 104, 99.8, 103.5)]        # sharp up: fill at the high channel, entry near the lows
    dip = [(103.5, 104, 100, 100.5)]      # Low hits the (tight) protective stop
    return warm + up + dip


def test_entry_and_channel_exit():
    sigs = _signals(Engine(_channel_cfg()), _breakout_then_drop())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_channel") in sigs


def test_protect_stop_exit():
    sigs = _signals(Engine(_protect_cfg()), _sharp_up_then_dip())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_protect_stop") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_channel_cfg())
    sigs = _signals(eng, _breakout_then_drop(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_without_breakout():
    # A falling series never breaks above the prior high channel -> no long.
    eng = Engine(_channel_cfg())
    sigs = _signals(eng, [(100 - i * 0.5, 100.5 - i * 0.5, 99 - i * 0.5, 99.5 - i * 0.5) for i in range(40)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
