"""Focused tests for the Displaced-Bollinger long engine.

Pure Python; no Nautilus, no network. The Bollinger mid-line is displaced back
``Disp`` bars while the band width is current: a break of the previous upper band
buys, a lower-band break sells. Runnable via ``pytest tests_platform -k
displaced_boll``.
"""
from __future__ import annotations

from strategies.displaced_boll_long.config import DisplacedBollLongConfig as Cfg
from strategies.displaced_boll_long.engine import DisplacedBollLongEngine as Engine


def _signals(eng, bars):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _fall_rise_drop():
    bars = []
    p = 200.0
    for _ in range(25):                    # steady fall -> displaced mid lags high
        p -= 1.0
        bars.append((p + 0.3, p + 0.3, p - 0.3, p))
    for _ in range(6):                     # sharp rise -> pierce the upper band
        p += 5.0
        bars.append((p - 0.5, p + 0.6, p - 0.6, p))
    for _ in range(20):                    # drop back down -> pierce the lower band
        p -= 3.0
        bars.append((p + 0.3, p + 0.4, p - 0.5, p))
    return bars


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg()), _fall_rise_drop())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_channel") in sigs


def test_no_entry_in_pure_downtrend():
    # A relentless fall keeps the high below the (lagging) upper band -> no long.
    eng = Engine(Cfg())
    sigs = _signals(eng, [(160 - i, 160.3 - i, 159.7 - i, 160 - i) for i in range(80)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_no_trade_during_warmup():
    # Fewer than disp+1 bars -> no displaced mid -> no bands -> no trade.
    eng = Engine(Cfg(avg_len=3, disp=16, sd_len=12, sdev=2.0))
    sigs = _signals(eng, [(100 + i, 100 + i + 5, 100 + i - 0.2, 100 + i) for i in range(15)])
    assert sigs == []
    assert eng.position == 0
