"""Focused tests for the Dual-MA (stop-and-reverse) engine.

Pure Python; no Nautilus, no network. An always-in-market system: long when the
fast SMA tops the slow SMA (previous bar), reverse to short when it drops below.
It emits sized :class:`TradeAction` s (rich-plan path) — one unit to open from
flat, two to reverse. Runnable via ``pytest tests_platform -k dual_ma``.
"""
from __future__ import annotations

from strategies.dual_ma.config import DualMaConfig as Cfg
from strategies.dual_ma.engine import DualMaEngine as Engine


def _run(cfg, closes, opens=None):
    eng = Engine(cfg)
    acted = []
    for i, c in enumerate(closes):
        o = c if opens is None else opens[i]
        label, actions, reason = eng.update(o, c)
        if actions:
            acted.append((label, actions[0].side, actions[0].quantity, reason))
    return acted, eng


def _rise_fall_rise():
    bars = []
    for i in range(30):
        bars.append(100 + i * 1.0)      # rising -> fast > slow -> long
    for i in range(30):
        bars.append(129 - i * 1.5)      # falling -> fast < slow -> reverse short
    for i in range(30):
        bars.append(85 + i * 1.5)       # rising -> reverse long
    return bars


def test_stop_and_reverse_sequence():
    acted, eng = _run(Cfg(), _rise_fall_rise())
    # Exactly three flips: open long (1u), reverse short (2u), reverse long (2u).
    assert [a[1] for a in acted] == ["BUY", "SELL", "BUY"]
    assert [a[2] for a in acted] == [1.0, 2.0, 2.0]
    assert [a[3] for a in acted] == ["enter_long", "reverse_to_short", "reverse_to_long"]
    assert eng.position == 1


def test_fires_once_per_regime_not_every_bar():
    # In a monotonic rise the fast MA stays above the slow MA, but the position
    # guard means only the first bar acts (no pyramiding).
    acted, eng = _run(Cfg(), [100 + i for i in range(60)])
    assert len(acted) == 1
    assert acted[0][1] == "BUY" and acted[0][2] == 1.0
    assert eng.position == 1


def test_fill_price_is_open():
    closes = _rise_fall_rise()
    opens = [c - 0.5 for c in closes]     # distinct open != close
    eng = Engine(Cfg())
    seen = []
    for c, o in zip(closes, opens):
        _, actions, _ = eng.update(o, c)
        if actions:
            seen.append(actions[0].fill_price)
    # every action fills at its bar's open
    assert seen and all(fp is not None for fp in seen)
    # match the opens of the acting bars
    idx = [i for i, c in enumerate(closes)]
    acted_opens = []
    eng2 = Engine(Cfg())
    for i, c in enumerate(closes):
        _, actions, _ = eng2.update(opens[i], c)
        if actions:
            acted_opens.append(opens[i])
    assert seen == acted_opens


def test_no_action_during_warmup():
    # Fewer than slow_length closes -> slow SMA is None -> no signal.
    acted, eng = _run(Cfg(fast_length=5, slow_length=20), [100 + i for i in range(15)])
    assert acted == []
    assert eng.position == 0
