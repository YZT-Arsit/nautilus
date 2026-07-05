"""Focused tests for the Average-Channel Range-Leader long engine.

Pure Python; no Nautilus, no network. A prior "range leader" bar (median over the
prior high with an expanding range) closing above the displaced high-MA buys; the
stop is the mid channel for the first ``ExitBar`` bars, then the outer (high)
channel. Uses tuned short periods (avg 8 / disp 2 / exit_bar 3). Runnable via
``pytest tests_platform -k avg_channel_range_leader``.
"""
from __future__ import annotations

from strategies.avg_channel_range_leader_long.config import AvgChannelRangeLeaderLongConfig as Cfg
from strategies.avg_channel_range_leader_long.engine import AvgChannelRangeLeaderLongEngine as Engine


def _cfg():
    return Cfg(avg_len=8, abs_disp=2, exit_bar=3)


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _rise_rangelead():
    bars = []
    p = 100.0
    for _ in range(15):
        p += 0.5
        bars.append((p - 0.4, p + 0.5, p - 0.5, p))
    bars.append((p, p + 4.0, p - 0.2, p + 3.5))    # RangeLead: wide, median over prior high
    p += 3.5
    bars.append((p - 0.1, p + 0.5, p - 0.3, p + 0.2))  # entry bar (long at open)
    p += 0.2
    return bars, p


def test_entry_and_mid_stop():
    # An immediate large drop (BarsSinceEntry <= ExitBar) trips the mid-channel
    # stop.
    bars, p = _rise_rangelead()
    bars.append((p, p + 0.2, p - 30.0, p - 5.0))
    sigs = _signals(Engine(_cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_mid_stop") in sigs


def test_entry_and_outer_stop():
    # A delayed drop (BarsSinceEntry > ExitBar) trips the outer (high) channel
    # stop instead.
    bars, p = _rise_rangelead()
    for _ in range(2):
        p += 0.4
        bars.append((p - 0.2, p + 0.4, p - 0.3, p))
    for _ in range(6):
        p -= 1.5
        bars.append((p + 0.3, p + 0.3, p - 0.6, p))
    sigs = _signals(Engine(_cfg()), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_outer_stop") in sigs


def test_no_entry_in_pure_downtrend():
    # A relentless fall never makes a range-leader up bar closing above the high
    # MA -> no long.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(160 - i, 160.3 - i, 159.7 - i, 160 - i) for i in range(60)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_no_trade_during_warmup():
    # Fewer than avg_len + abs_disp bars -> no displaced channel -> no trade.
    eng = Engine(_cfg())
    sigs = _signals(eng, [(100 + i, 100 + i + 1, 100 + i - 0.2, 100 + i) for i in range(6)])
    assert sigs == []
    assert eng.position == 0
