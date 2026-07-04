"""Focused tests for the Swinger short engine.

Pure Python; no Nautilus, no network. The entry needs a specific configuration —
price below the long trend MA while the fast-slow price oscillator is still
positive but weakening (a failing bounce inside a longer downtrend) — so the
crafted path is a downtrend, a sub-trend bounce that lifts the oscillator
positive while price stays below the lagging 50-MA, the bounce rolling over
(entry), then a renewed pop that turns momentum back up and breaks the 3-bar
high (cover). Runnable via ``pytest tests_platform -k swinger``.
"""
from __future__ import annotations

from strategies.swinger_short.config import SwingerShortConfig as Cfg
from strategies.swinger_short.engine import SwingerShortEngine as Engine


def _bar(p, up=0.5, dn=0.5):
    return (p, p + up, p - dn, p)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _failing_bounce_path():
    """Downtrend -> sub-trend bounce (osci turns positive, price stays below the
    50-MA) -> bounce rolls over (short) -> renewed pop breaks the 3-bar high (cover)."""
    bars = [_bar(200 - i * 1.2) for i in range(60)]          # long downtrend
    base = 200 - 59 * 1.2
    bars += [_bar(base + i * 1.6) for i in range(10)]        # bounce: fast>slow -> osci>0
    top = base + 9 * 1.6
    bars += [_bar(top - i * 0.6) for i in range(8)]          # rollover: osci weakens while >=0
    bot = top - 7 * 0.6
    bars += [_bar(bot + i * 2.0, up=1.2, dn=0.3) for i in range(12)]  # pop: osci up, break highs
    return bars


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg()), _failing_bounce_path())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_cover") in sigs


def test_entry_requires_volume():
    eng = Engine(Cfg())
    sigs = _signals(eng, _failing_bounce_path(), volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_short_in_uptrend():
    # A steady uptrend keeps price above the trend MA -> never shorts.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(100 + i) for i in range(90)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
