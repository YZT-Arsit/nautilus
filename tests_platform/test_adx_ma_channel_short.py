"""Focused tests for the ADX + MA-channel short engine.

Pure Python; no Nautilus, no network. Ported from TradeBlazer
``ADXandMAChannelSys_S``: a rising Wilder ADX plus a close below the low EMA arms a
sell-setup with a channel-width breakout target; a break of that target (while the
setup has just lapsed, ``MROSS[1] <> 0``) shorts, and a rally back above the prior
low EMA covers. Entry is gated by the source's ``CurrentBar > 100`` warm-up, so the
sequences pre-roll ~105 bars. Runnable via
``pytest tests_platform -k adx_ma_channel``.
"""
from __future__ import annotations

from strategies.adx_ma_channel_short.config import AdxMaChannelShortConfig as Cfg
from strategies.adx_ma_channel_short.engine import AdxMaChannelShortEngine as Engine


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
    """~110 bars: choppy downtrend past the CurrentBar>100 gate, then an impulse
    down (arms the setup + target), a pause bar (setup lapses -> MROSS becomes 1),
    and a spike-down trigger bar that pierces the target -> short at bar 109."""
    bars = []
    p = 100.0
    for i in range(105):
        p -= 0.15
        if i % 2 == 0:
            bars.append((p + 0.1, p + 0.5, p - 0.5, p - 0.2)); p -= 0.2
        else:
            bars.append((p - 0.1, p + 0.4, p - 0.4, p + 0.1)); p += 0.1
    for _ in range(3):
        p -= 3.0
        bars.append((p + 0.5, p + 0.6, p - 1.5, p))     # impulse down: arm setup
    p += 0.5
    bars.append((p, p + 1.2, p - 0.2, p + 1.0)); p += 1.0  # pause: setup lapses
    p -= 6.0
    bars.append((p + 4.0, p + 4.2, p - 2.0, p))         # trigger: pierce target
    return bars, p


def test_entry_shorts_on_target_break():
    eng = Engine(_cfg())
    sigs = _signals(eng, _base()[0])
    assert ("SELL", "enter_short") in sigs
    assert eng.position == -1


def test_exit_covers_on_ema_break():
    # A sharp rally after the short breaks back above the prior low EMA -> cover.
    bars, p = _base()
    for _ in range(6):
        p += 5.0
        bars.append((p - 4.0, p + 0.5, p - 4.5, p))
    eng = Engine(_cfg())
    sigs = _signals(eng, bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_ema_break") in sigs
    assert eng.position == 0


def test_stays_short_while_falling():
    # A continued decline never breaks the low EMA -> the short is held.
    bars, p = _base()
    for _ in range(6):
        p -= 2.0
        bars.append((p + 0.3, p + 0.4, p - 1.0, p))
    eng = Engine(_cfg())
    sigs = _signals(eng, bars)
    assert sigs == [("SELL", "enter_short")]
    assert eng.position == -1


def test_no_entry_during_warmup():
    # Even a strong decline within the first 100 bars cannot enter (CurrentBar>100).
    eng = Engine(_cfg())
    sigs = _signals(eng, [(120 - i, 120.4 - i, 119.0 - i, 119.5 - i) for i in range(40)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
