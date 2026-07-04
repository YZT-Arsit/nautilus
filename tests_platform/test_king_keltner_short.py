"""Focused tests for the King Keltner short engine.

Pure Python; no Nautilus, no network. A typical-price MA with a lower ATR band: a
downward-turning MA plus a break below the prior lower band opens a short, and a
break back above the prior MA covers. Runnable via ``pytest tests_platform -k
king_keltner``.
"""
from __future__ import annotations

from strategies.king_keltner_short.config import KingKeltnerShortConfig as Cfg
from strategies.king_keltner_short.engine import KingKeltnerShortEngine as Engine


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _rise_drop_bounce():
    up = [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(8)]      # MA rising
    drop = [(107 - i * 2.0, 107 - i * 2.0 + 0.2, 107 - i * 2.0 - 2.5, 107 - i * 2.0 - 2.0)
            for i in range(6)]                                            # MA turns down, breaks band
    bounce = [(96, 103, 95.5, 102)]                                       # High back above MA -> cover
    return up + drop + bounce


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg(avg_length=5, atr_length=5)), _rise_drop_bounce())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_cover") in sigs


def test_no_entry_in_pure_uptrend():
    # A rising MA never turns down -> no short.
    eng = Engine(Cfg(avg_length=5, atr_length=5))
    sigs = _signals(eng, [(100 + i, 100.3 + i, 99.7 + i, 100 + i) for i in range(40)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


def test_entry_and_exit_never_same_bar():
    # Exit is gated by bars_since_entry >= 1: a short must open before it covers.
    eng = Engine(Cfg(avg_length=5, atr_length=5))
    sigs = _signals(eng, _rise_drop_bounce())
    kinds = [r for _, r in sigs]
    assert kinds.index("enter_short") < kinds.index("exit_cover")
