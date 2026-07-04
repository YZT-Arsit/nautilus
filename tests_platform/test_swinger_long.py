"""Focused tests for the Swinger long engine (mirror of short).

Pure Python; no Nautilus, no network. The entry needs a specific configuration —
price above the long trend MA while the fast-slow price oscillator is still
negative but strengthening (a failing dip inside a longer uptrend) — so the
crafted path is an uptrend, a sub-trend dip that pushes the oscillator negative
while price stays above the lagging 50-MA, the dip recovering (entry), then a
renewed drop that turns momentum back down and breaks the 3-bar low (sell).
Runnable via ``pytest tests_platform -k swinger``.
"""
from __future__ import annotations

from strategies.swinger_long.config import SwingerLongConfig as Cfg
from strategies.swinger_long.engine import SwingerLongEngine as Engine


def _bar(p, up=0.5, dn=0.5):
    return (p, p + up, p - dn, p)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _failing_dip_path():
    """Uptrend -> sub-trend dip (osci turns negative, price stays above the
    50-MA) -> dip recovers (long) -> renewed drop breaks the 3-bar low (sell)."""
    bars = [_bar(100 + i * 1.2) for i in range(60)]         # long uptrend
    base = 100 + 59 * 1.2
    bars += [_bar(base - i * 1.6) for i in range(10)]        # dip: fast<slow -> osci<0
    bot = base - 9 * 1.6
    bars += [_bar(bot + i * 0.6) for i in range(8)]          # recovery: osci strengthens while <=0
    top = bot + 7 * 0.6
    bars += [_bar(top - i * 2.0, up=0.3, dn=1.2) for i in range(12)]  # drop: osci down, break lows
    return bars


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg()), _failing_dip_path())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_sell") in sigs


def test_entry_requires_volume():
    eng = Engine(Cfg())
    sigs = _signals(eng, _failing_dip_path(), volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_long_in_downtrend():
    # A steady downtrend keeps price below the trend MA -> never longs.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bar(200 - i) for i in range(90)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
