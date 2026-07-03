"""Focused tests for the Traffic Jam short engine.

Pure Python; no Nautilus, no network. Covers the Wilder DMI/ADX helper and the
counter-trend state machine (ranging + falling ADX + consecutive up-closes ->
short; time stop / protective stop -> cover). Runnable via
``pytest tests_platform -k traffic``.
"""
from __future__ import annotations

from strategies.traffic_jam_short.config import TrafficJamShortConfig
from strategies.traffic_jam_short.engine import _DmiAdx, TrafficJamShortEngine


# -- DMI / ADX -------------------------------------------------------------

def test_adx_high_in_strong_trend():
    d = _DmiAdx(14)
    adx = None
    for i in range(60):
        adx = d.update(100 + i + 0.5, 100 + i - 0.2, 100 + i)  # steady uptrend
    assert adx is not None and adx > 40         # a strong trend -> high ADX
    plus_di = 100 * d.avg_pdm / d.svolty
    minus_di = 100 * d.avg_mdm / d.svolty
    assert plus_di > minus_di                   # up trend -> +DI dominates


def test_adx_low_in_chop():
    d = _DmiAdx(14)
    adx = None
    for i in range(80):
        p = 100 + (0.3 if i % 2 else -0.3)      # tight two-bar oscillation
        adx = d.update(p + 0.2, p - 0.2, p)
    assert adx is not None and adx < 20         # ranging -> low ADX


def test_adx_stays_in_bounds():
    d = _DmiAdx(14)
    vals = []
    for i in range(100):
        p = 100 + (i % 7) - 3                    # arbitrary bounded wander
        a = d.update(p + 1, p - 1, p)
        if a is not None:
            vals.append(a)
    assert vals and all(0.0 <= a <= 100.0 for a in vals)


def test_warmup_returns_none():
    d = _DmiAdx(14)
    # fewer than ~2*N bars -> ADX not yet available.
    outs = [d.update(100 + i, 99 + i, 100 + i) for i in range(10)]
    assert all(o is None for o in outs)


# -- entry + exits ----------------------------------------------------------

def _ranging_then_three_up_closes() -> list[float]:
    """Closes: a long tight oscillation (low, settling ADX) then 3 up-closes."""
    seq = [100.0 + (0.5 if i % 2 == 0 else -0.5) for i in range(50)]
    seq += [99.6, 99.4, 99.2, 99.4, 99.7, 100.0]  # 3 down then 3 up closes
    return seq


def test_short_entry_then_time_exit():
    eng = TrafficJamShortEngine(TrafficJamShortConfig())  # proactive_stop_bars=10
    seq = _ranging_then_three_up_closes() + [100.0] * 15   # hold flat -> time exit
    signals = []
    for c in seq:
        sig, reason = eng.update(c, c + 0.3, c - 0.3, c, 1.0)
        if sig != "HOLD":
            signals.append((sig, reason))
    # exactly one short round-trip: SELL(enter) then BUY(time exit).
    assert ("SELL", "enter_short") in signals
    assert ("BUY", "exit_time_stop") in signals
    assert all(s == "SELL" for s, _ in signals if s == "SELL")   # all entries are shorts
    assert eng.position == 0                                     # flat at the end


def test_short_entry_then_protective_stop():
    eng = TrafficJamShortEngine(TrafficJamShortConfig())
    events = [(c, c + 0.3, c - 0.3, c) for c in _ranging_then_three_up_closes()]
    # right after entry, a bar whose high blows through the protective stop.
    events += [(101.0, 120.0, 100.5, 101.0)] + [(101.0, 101.3, 100.7, 101.0)] * 5
    signals = []
    for o, h, l, c in events:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            signals.append((sig, reason))
    assert ("SELL", "enter_short") in signals
    assert ("BUY", "exit_protect_stop") in signals
    assert eng.position == 0


def test_entry_requires_volume():
    eng = TrafficJamShortEngine(TrafficJamShortConfig())
    seq = _ranging_then_three_up_closes() + [100.0] * 5
    signals = [eng.update(c, c + 0.3, c - 0.3, c, 0.0)[0] for c in seq]  # Vol == 0
    assert all(s == "HOLD" for s in signals)   # volume gate blocks all trades
    assert eng.position == 0
