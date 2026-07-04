"""Focused tests for the MA-Crossover Channel-Breakout long engine.

Pure Python; no Nautilus, no network. A golden cross arms a channel-breakout long
entry; the position exits either on a trend-reversal (death-cross channel break)
or a periodic-low trailing stop, and re-enters on a fresh breakout within N bars
after a trailing stop. Runnable via ``pytest tests_platform -k
ma_crossover_channel``.
"""
from __future__ import annotations

from strategies.ma_crossover_channel_long.config import MaCrossoverChannelLongConfig as Cfg
from strategies.ma_crossover_channel_long.engine import MaCrossoverChannelLongEngine as Engine


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
    # A far trailing stop (trail_bar large) isolates the reversal exit.
    return Cfg(fast_len=3, slow_len=6, ch_len=4, extra_percentage=100,
               trail_bar=30, re_bars=15, re_entry_ch_len=4)


def _up_pullback_up():
    down = [(100 - i, 100.3 - i, 99.7 - i, 100 - i) for i in range(8)]
    up = [(92 + i * 1.5, 92 + i * 1.5 + 1.0, 92 + i * 1.5 - 0.3, 92 + i * 1.5 + 0.8) for i in range(10)]
    pull = [(106, 106.2, 98, 99)]                  # trips the trailing stop
    recover = [(99 + i * 2.0, 99 + i * 2.0 + 2.0, 99 + i * 2.0 - 0.2, 99 + i * 2.0 + 1.8) for i in range(6)]
    return down + up + pull + recover


def _up_then_reverse():
    down = [(100 - i, 100.3 - i, 99.7 - i, 100 - i) for i in range(8)]
    up = [(92 + i * 1.5, 92 + i * 1.5 + 1.0, 92 + i * 1.5 - 0.3, 92 + i * 1.5 + 0.8) for i in range(10)]
    rollover = [(106 - i * 2.5, 106 - i * 2.5 + 0.3, 106 - i * 2.5 - 2.0, 106 - i * 2.5 - 1.5) for i in range(12)]
    return down + up + rollover


def test_initial_entry_trailing_stop_and_reentry():
    sigs = _signals(Engine(_trail_and_reentry_cfg()), _up_pullback_up())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_trail_stop") in sigs
    assert ("BUY", "enter_reentry") in sigs


def test_reverse_exit():
    sigs = _signals(Engine(_reverse_cfg()), _up_then_reverse())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_reverse") in sigs


def test_no_trades_on_zero_volume():
    eng = Engine(_trail_and_reentry_cfg())
    sigs = _signals(eng, _up_pullback_up(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_entry_in_pure_downtrend():
    # No golden cross -> the long breakout is never armed -> no entry.
    eng = Engine(_trail_and_reentry_cfg())
    sigs = _signals(eng, [(200 - i, 200.3 - i, 199.7 - i, 200 - i) for i in range(50)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
