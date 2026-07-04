"""Focused tests for the Keltner Channel short engine.

Pure Python; no Nautilus, no network. A close crossing below the lower Keltner
band arms a short trigger ``sellN`` bars ahead; a break of the trigger opens the
short. The position covers on a close back above the mid on the prior bar, or on
a break above the recent N-bar high. Runnable via ``pytest tests_platform -k
keltner_channel``.
"""
from __future__ import annotations

from strategies.keltner_channel_short.config import KeltnerChannelShortConfig as Cfg
from strategies.keltner_channel_short.engine import KeltnerChannelShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _stop_cfg():
    return Cfg(length=5, constt=1.2, chan_pcnt=0.5, sell_n=5, stop_n=4)


def _mid_cross_cfg():
    # A large stop_n anchors the protective stop to an earlier high plateau, so a
    # modest recovery crosses the mid without tripping the stop first.
    return Cfg(length=5, constt=1.2, chan_pcnt=0.5, sell_n=5, stop_n=40)


def _break_then_spike():
    warm = [(100, 100.4, 99.6, 100 + (0.2 if i % 2 else -0.2)) for i in range(8)]
    cross = [(100, 100.2, 96, 96.2)]          # close crosses below lower band -> arm trigger
    enter = [(96, 96.2, 93, 94)]              # Low breaks the trigger -> short
    hold = [(94, 94.3, 92, 92.5)]             # stay below the mid
    spike = [(92, 120, 91.5, 93)]             # High spikes above the recent high -> stop
    return warm + cross + enter + hold + spike


def _break_then_recover():
    warm = [(107, 108, 106.5, 107 + (0.2 if i % 2 else -0.2)) for i in range(8)]
    cross = [(107, 107.2, 96, 96.2)]
    enter = [(96, 96.2, 93, 94)]
    below = [(94, 95, 93.5, 94.5)]
    cross_up = [(95, 100, 94.5, 99.5)]        # close crosses above the mid (con2 set)
    exit_bar = [(99, 100, 98.5, 99.6)]        # con2[1] true -> mid-cross cover
    return warm + cross + enter + below + cross_up + exit_bar


def test_entry_and_stop_exit():
    sigs = _signals(Engine(_stop_cfg()), _break_then_spike())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_stop") in sigs


def test_mid_cross_exit():
    sigs = _signals(Engine(_mid_cross_cfg()), _break_then_recover())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_mid_cross") in sigs


def test_no_entry_without_band_break():
    # A quiet series never crosses below the lower band -> the trigger is never
    # armed -> no short.
    eng = Engine(_stop_cfg())
    sigs = _signals(eng, [(100, 100.3, 99.7, 100) for _ in range(40)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
