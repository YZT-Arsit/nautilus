"""Focused tests for the Average-Channel Range-Leader short engine.

Pure Python; no Nautilus, no network. A prior "range leader" bar (median under the
prior low with an expanding range) closing below the displaced low-MA shorts; the
stop is the mid channel for the first ``ExitBar`` bars, then the outer (low)
channel. Uses tuned short periods (avg 8 / disp 2 / exit_bar 3). Runnable via
``pytest tests_platform -k avg_channel_range_leader``.
"""
from __future__ import annotations

from strategies.avg_channel_range_leader_short.config import AvgChannelRangeLeaderShortConfig as Cfg
from strategies.avg_channel_range_leader_short.engine import AvgChannelRangeLeaderShortEngine as Engine


def _cfg():
    return Cfg(avg_len=8, abs_disp=2, exit_bar=3)


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _decline_rangelead():
    # gentle decline sets the displaced channels, then a wide down bar (range
    # leader), then the entry bar.
    bars = []
    p = 120.0
    for _ in range(15):
        p -= 0.5
        bars.append((p + 0.4, p + 0.5, p - 0.5, p))
    bars.append((p, p + 0.2, p - 4.0, p - 3.5))    # RangeLead: wide, median under prior low
    p -= 3.5
    bars.append((p + 0.1, p + 0.3, p - 0.5, p - 0.2))  # entry bar (short at open)
    p -= 0.2
    return bars, p


def test_entry_and_mid_stop():
    # An immediate large bounce (BarsSinceEntry <= ExitBar) trips the mid-channel
    # stop.
    bars, p = _decline_rangelead()
    bars.append((p, p + 30.0, p - 0.2, p + 5.0))
    sigs = _signals(Engine(_cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_mid_stop") in sigs


def test_entry_and_outer_stop():
    # A delayed bounce (BarsSinceEntry > ExitBar) trips the outer (low) channel
    # stop instead.
    bars, p = _decline_rangelead()
    for _ in range(2):
        p -= 0.4
        bars.append((p + 0.2, p + 0.3, p - 0.4, p))
    for _ in range(6):
        p += 1.5
        bars.append((p - 0.3, p + 0.6, p - 0.3, p))
    sigs = _signals(Engine(_cfg()), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_outer_stop") in sigs


def test_no_entry_in_pure_uptrend():
    # A relentless rise never makes a range-leader down bar closing below the low
    # MA -> no short.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(60)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_no_trade_during_warmup():
    # Fewer than avg_len + abs_disp bars -> no displaced channel -> no trade.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(120 - i, 120 - i + 0.2, 120 - i - 1, 120 - i) for i in range(6)])
    assert sigs == []
    assert eng.position == 0
