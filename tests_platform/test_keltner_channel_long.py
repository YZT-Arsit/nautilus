"""Focused tests for the Keltner Channel long engine (mirror of short).

Pure Python; no Nautilus, no network. A close crossing above the upper Keltner
band arms a long trigger ``buyN`` bars ahead; a break of the trigger opens the
long. The position sells on a close back below the mid on the prior bar, or on a
break below the recent N-bar low. Runnable via ``pytest tests_platform -k
keltner_channel``.
"""
from __future__ import annotations

from strategies.keltner_channel_long.config import KeltnerChannelLongConfig as Cfg
from strategies.keltner_channel_long.engine import KeltnerChannelLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _stop_cfg():
    return Cfg(length=5, constt=1.2, chan_pcnt=0.5, buy_n=5, stop_n=4)


def _mid_cross_cfg():
    # A large stop_n anchors the protective stop to an earlier low plateau, so a
    # modest pullback crosses the mid without tripping the stop first.
    return Cfg(length=5, constt=1.2, chan_pcnt=0.5, buy_n=5, stop_n=40)


def _break_then_plunge():
    warm = [(100, 100.4, 99.6, 100 + (0.2 if i % 2 else -0.2)) for i in range(8)]
    cross = [(100, 104, 99.8, 103.8)]         # close crosses above upper band -> arm trigger
    enter = [(104, 107, 103.5, 106)]          # High breaks the trigger -> long
    hold = [(106, 106.5, 105, 105.5)]         # stay above the mid
    plunge = [(105, 105.5, 88, 90)]           # Low plunges below the recent low -> stop
    return warm + cross + enter + hold + plunge


def _break_then_pullback():
    warm = [(93, 93.5, 92, 93 + (0.2 if i % 2 else -0.2)) for i in range(8)]
    cross = [(93, 104, 92.8, 103.8)]
    enter = [(104, 107, 103.5, 106)]
    above = [(106, 107, 105, 106)]
    cross_dn = [(106, 106.5, 100, 100.5)]     # close crosses below the mid (con2 set)
    exit_bar = [(100, 101, 99.5, 100.4)]      # con2[1] true -> mid-cross sell
    return warm + cross + enter + above + cross_dn + exit_bar


def test_entry_and_stop_exit():
    sigs = _signals(Engine(_stop_cfg()), _break_then_plunge())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_stop") in sigs


def test_mid_cross_exit():
    sigs = _signals(Engine(_mid_cross_cfg()), _break_then_pullback())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_mid_cross") in sigs


def test_no_entry_without_band_break():
    # A quiet series never crosses above the upper band -> the trigger is never
    # armed -> no long.
    eng = Engine(_stop_cfg())
    sigs = _signals(eng, [(100, 100.3, 99.7, 100) for _ in range(40)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
