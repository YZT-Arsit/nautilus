"""Focused tests for the King Keltner long engine (mirror of short).

Pure Python; no Nautilus, no network. A typical-price MA with an upper ATR band:
an upward-turning MA plus a break above the prior upper band opens a long, and a
break back below the prior MA flattens it. Runnable via ``pytest tests_platform
-k king_keltner``.
"""
from __future__ import annotations

from strategies.king_keltner_long.config import KingKeltnerLongConfig as Cfg
from strategies.king_keltner_long.engine import KingKeltnerLongEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _drop_rise_dip():
    down = [(100 - i, 100.3 - i, 99.7 - i, 100 - i) for i in range(8)]        # MA falling
    rise = [(93 + i * 2.0, 93 + i * 2.0 + 2.5, 93 + i * 2.0 - 0.2, 93 + i * 2.0 + 2.0)
            for i in range(6)]                                               # MA turns up, breaks band
    dip = [(104, 104.5, 97, 98)]                                             # Low back below MA -> sell
    return down + rise + dip


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg(avg_length=5, atr_length=5)), _drop_rise_dip())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_sell") in sigs


def test_no_entry_in_pure_downtrend():
    # A falling MA never turns up -> no long.
    eng = Engine(Cfg(avg_length=5, atr_length=5))
    sigs = _signals(eng, [(200 - i, 200.3 - i, 199.7 - i, 200 - i) for i in range(40)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    # Exit is gated by bars_since_entry >= 1: a long must open before it sells.
    eng = Engine(Cfg(avg_length=5, atr_length=5))
    sigs = _signals(eng, _drop_rise_dip())
    kinds = [r for _, r in sigs]
    assert kinds.index("enter_long") < kinds.index("exit_sell")
