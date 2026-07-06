"""Focused tests for the ADX + MA-channel long engine.

Pure Python; no Nautilus, no network. Ported from TradeBlazer
``ADXandMAChannelSys_L`` (long mirror of the short): a rising Wilder ADX plus a
close above the high EMA arms a buy-setup with a channel-width breakout target; a
break of that target (while the setup has just lapsed, ``MROBS[1] <> 0``) buys, and
a drop back below the prior high EMA sells. Entry is gated by the source's
``CurrentBar > 100`` warm-up, so the sequences pre-roll ~105 bars. Runnable via
``pytest tests_platform -k adx_ma_channel``.
"""
from __future__ import annotations

from strategies.adx_ma_channel_long.config import AdxMaChannelLongConfig as Cfg
from strategies.adx_ma_channel_long.engine import AdxMaChannelLongEngine as Engine


def _cfg():
    return Cfg(dmi_n=14, dmi_m=30, avg_len=30, entry_bar=2, tick=0.01)


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _base():
    """~110 bars: choppy uptrend past the CurrentBar>100 gate, then an impulse up
    (arms the setup + target), a pause bar (setup lapses -> MROBS becomes 1), and a
    spike-up trigger bar that pierces the target -> long at bar 109."""
    bars = []
    p = 100.0
    for i in range(105):
        p += 0.15
        if i % 2 == 0:
            bars.append((p - 0.1, p + 0.5, p - 0.5, p + 0.2)); p += 0.2
        else:
            bars.append((p + 0.1, p + 0.4, p - 0.4, p - 0.1)); p -= 0.1
    for _ in range(3):
        p += 3.0
        bars.append((p - 0.5, p + 1.5, p - 0.6, p))     # impulse up: arm setup
    p -= 0.5
    bars.append((p, p + 0.2, p - 1.2, p - 1.0)); p -= 1.0  # pause: setup lapses
    p += 6.0
    bars.append((p - 4.0, p + 2.0, p - 4.2, p))         # trigger: pierce target
    return bars, p


def test_entry_longs_on_target_break():
    eng = Engine(_cfg())
    sigs = _signals(eng, _base()[0])
    assert ("BUY", "enter_long") in sigs
    assert eng.position == 1


def test_exit_sells_on_ema_break():
    # A sharp drop after the long breaks back below the prior high EMA -> sell.
    bars, p = _base()
    for _ in range(6):
        p -= 5.0
        bars.append((p + 4.0, p + 4.5, p - 0.5, p))
    eng = Engine(_cfg())
    sigs = _signals(eng, bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_ema_break") in sigs
    assert eng.position == 0


def test_stays_long_while_rising():
    # A continued advance never breaks the high EMA -> the long is held.
    bars, p = _base()
    for _ in range(6):
        p += 2.0
        bars.append((p - 0.3, p + 1.0, p - 0.4, p))
    eng = Engine(_cfg())
    sigs = _signals(eng, bars)
    assert sigs == [("BUY", "enter_long")]
    assert eng.position == 1


def test_no_entry_during_warmup():
    # Even a strong advance within the first 100 bars cannot enter (CurrentBar>100).
    eng = Engine(_cfg())
    sigs = _signals(eng, [(80 + i, 81.0 + i, 79.6 + i, 80.5 + i) for i in range(40)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
