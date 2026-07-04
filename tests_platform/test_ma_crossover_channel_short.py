"""Focused tests for the MA-Crossover Channel-Breakout short engine (mirror).

Pure Python; no Nautilus, no network. A death cross arms a channel-breakout short
entry; the position covers either on a trend-reversal (golden-cross channel break)
or a periodic-high trailing stop, and re-enters on a fresh breakdown within N bars
after a trailing stop. Runnable via ``pytest tests_platform -k
ma_crossover_channel``.

Note the reverse test uses a tiny ``extra_percentage`` because the TB source's
reverse line uses a ``0.01`` factor (verbatim); with the default 300 the reverse
line is ``HH * 4`` and never fires (see engine docstring).
"""
from __future__ import annotations

from strategies.ma_crossover_channel_short.config import MaCrossoverChannelShortConfig as Cfg
from strategies.ma_crossover_channel_short.engine import MaCrossoverChannelShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _trail_and_reentry_cfg():
    return Cfg(fast_len=3, slow_len=6, ch_len=4, extra_percentage=100,
               trail_bar=3, re_bars=15, re_entry_ch_len=4)


def _reverse_cfg():
    # Tiny extra_percentage makes the 0.01-buffered reverse line reachable; a far
    # trailing stop (trail_bar large) isolates the reversal exit.
    return Cfg(fast_len=3, slow_len=6, ch_len=4, extra_percentage=1,
               trail_bar=50, re_bars=15, re_entry_ch_len=4)


def _down_bounce_down():
    up = [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(8)]
    down = [(108 - i * 1.5, 108 - i * 1.5 + 0.3, 108 - i * 1.5 - 1.0, 108 - i * 1.5 - 0.8) for i in range(10)]
    bounce = [(94, 102, 93.8, 101)]                # trips the (highest-high) trailing stop
    renew = [(101 - i * 2.0, 101 - i * 2.0 + 0.2, 101 - i * 2.0 - 2.0, 101 - i * 2.0 - 1.8) for i in range(6)]
    return up + down + bounce + renew


def _down_then_reverse():
    up = [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(8)]
    down = [(108 - i * 1.5, 108 - i * 1.5 + 0.3, 108 - i * 1.5 - 1.0, 108 - i * 1.5 - 0.8) for i in range(10)]
    rollover = [(94 + i * 2.5, 94 + i * 2.5 + 2.0, 94 + i * 2.5 - 0.3, 94 + i * 2.5 + 1.5) for i in range(12)]
    return up + down + rollover


def test_initial_entry_trailing_stop_and_reentry():
    sigs = _signals(Engine(_trail_and_reentry_cfg()), _down_bounce_down())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_trail_stop") in sigs
    assert ("SELL", "enter_reentry") in sigs


def test_reverse_exit():
    sigs = _signals(Engine(_reverse_cfg()), _down_then_reverse())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_reverse") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_trail_and_reentry_cfg())
    sigs = _signals(eng, _down_bounce_down(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_uptrend():
    # No death cross -> the short breakout is never armed -> no entry.
    eng = Engine(_trail_and_reentry_cfg())
    sigs = _signals(eng, [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(50)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
