"""Focused tests for the Spread Channel Breakout short engine.

Pure Python; no Nautilus, no network. The engine runs a channel breakout on a
single (spread) OHLC series; bars use ``OO == CC`` so HH/LL collapse to the price
and the channel maths are easy to reason about. A ranging warmup builds the
channel, a break below the lower channel opens the short, and the two covers
(reverse-above-upper, stop-above the tighter stop channel) are isolated by what
the rebound does. Runnable via ``pytest tests_platform -k spread_channel``.
"""
from __future__ import annotations

import math

from strategies.spread_channel_breakout_short.config import SpreadChannelBreakoutShortConfig as Cfg
from strategies.spread_channel_breakout_short.engine import SpreadChannelBreakoutShortEngine as Engine


def _bar(p):
    return (p, p, p, p)  # OO == CC -> HH == LL == p


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def test_entry_then_stop_cover():
    # Range high ~108 (20-bar upper out of reach); break to a new low -> short;
    # a slow sub-upper rise lets the 10-bar stop channel catch the close.
    rng = [_bar(104 + 4 * math.sin(i)) for i in range(24)]
    slow = [_bar(90)] + [_bar(90 + i * 0.7) for i in range(1, 18)]
    sigs = _signals(Engine(Cfg()), rng + slow)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "stop_cover") in sigs


def test_entry_then_reverse_cover():
    # Break to a new low -> short; a one-bar jump above the 20-bar upper channel
    # triggers the reverse cover before the stop channel is reached.
    rng = [_bar(100 + 2 * math.sin(i)) for i in range(24)]
    bars = rng + [_bar(94), _bar(93), _bar(92)] + [_bar(130)] + [_bar(130) for _ in range(3)]
    sigs = _signals(Engine(Cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "reverse_cover") in sigs


def test_entry_requires_volume():
    rng = [_bar(100 + 2 * math.sin(i)) for i in range(24)]
    bars = rng + [_bar(94), _bar(93), _bar(92)] + [_bar(130)] + [_bar(130) for _ in range(3)]
    eng = Engine(Cfg())
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_short_in_uptrend():
    # A steady rise never breaks below the lower channel -> never shorts.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(100 + i) for i in range(60)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
