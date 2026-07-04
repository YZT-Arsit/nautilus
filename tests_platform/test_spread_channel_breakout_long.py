"""Focused tests for the Spread Channel Breakout long engine (mirror of short).

Pure Python; no Nautilus, no network. The engine runs a channel breakout on a
single (spread) OHLC series; bars use ``OO == CC`` so HH/LL collapse to the price
and the channel maths are easy to reason about. A ranging warmup builds the
channel, a break above the upper channel opens the long, and the two exits
(reverse-below-lower, stop-below the tighter stop channel) are isolated by what
the pullback does. Runnable via ``pytest tests_platform -k spread_channel``.
"""
from __future__ import annotations

import math

from strategies.spread_channel_breakout_long.config import SpreadChannelBreakoutLongConfig as Cfg
from strategies.spread_channel_breakout_long.engine import SpreadChannelBreakoutLongEngine as Engine


def _bar(p):
    return (p, p, p, p)  # OO == CC -> HH == LL == p


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def test_entry_then_stop_exit():
    # Range low ~92 (20-bar lower out of reach); break to a new high -> long;
    # a slow sub-lower decline lets the 10-bar stop channel catch the close.
    rng = [_bar(96 + 4 * math.sin(i)) for i in range(24)]
    slow = [_bar(110)] + [_bar(110 - i * 0.7) for i in range(1, 18)]
    sigs = _signals(Engine(Cfg()), rng + slow)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "stop_exit") in sigs


def test_entry_then_reverse_exit():
    # Break to a new high -> long; a one-bar plunge below the 20-bar lower channel
    # triggers the reverse exit before the stop channel is reached.
    rng = [_bar(100 + 2 * math.sin(i)) for i in range(24)]
    bars = rng + [_bar(106), _bar(107), _bar(108)] + [_bar(70)] + [_bar(70) for _ in range(3)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "reverse_exit") in sigs


def test_entry_requires_volume():
    rng = [_bar(100 + 2 * math.sin(i)) for i in range(24)]
    bars = rng + [_bar(106), _bar(107), _bar(108)] + [_bar(70)] + [_bar(70) for _ in range(3)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_long_in_downtrend():
    # A steady decline never breaks above the upper channel -> never longs.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(200 - i) for i in range(60)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
