"""Focused tests for the Traffic Jam long engine (mirror of the short tests).

Pure Python; no Nautilus, no network. Covers the Wilder DMI/ADX helper and the
counter-trend state machine (ranging + falling ADX + consecutive down-closes ->
long; time stop / protective stop -> flatten). Runnable via
``pytest tests_platform -k traffic``.
"""
from __future__ import annotations

from strategies.traffic_jam_long.config import TrafficJamLongConfig
from strategies.traffic_jam_long.engine import _DmiAdx, TrafficJamLongEngine


# -- DMI / ADX -------------------------------------------------------------

def test_adx_high_in_strong_trend():
    d = _DmiAdx(14)
    adx = None
    for i in range(60):
        adx = d.update(100 - i + 0.2, 100 - i - 0.5, 100 - i)  # steady downtrend
    assert adx is not None and adx > 40
    minus_di = 100 * d.avg_mdm / d.svolty
    plus_di = 100 * d.avg_pdm / d.svolty
    assert minus_di > plus_di                   # down trend -> -DI dominates


def test_adx_low_in_chop():
    d = _DmiAdx(14)
    adx = None
    for i in range(80):
        p = 100 + (0.3 if i % 2 else -0.3)
        adx = d.update(p + 0.2, p - 0.2, p)
    assert adx is not None and adx < 20


def test_adx_stays_in_bounds():
    d = _DmiAdx(14)
    vals = []
    for i in range(100):
        p = 100 + (i % 7) - 3
        a = d.update(p + 1, p - 1, p)
        if a is not None:
            vals.append(a)
    assert vals and all(0.0 <= a <= 100.0 for a in vals)


# -- entry + exits ----------------------------------------------------------

def _ranging_then_three_down_closes() -> list[float]:
    """Closes: a long tight oscillation (low, settling ADX) then 3 down-closes."""
    seq = [100.0 + (0.5 if i % 2 == 0 else -0.5) for i in range(50)]
    seq += [100.4, 100.6, 100.8, 100.6, 100.3, 100.0]  # 3 up then 3 down closes
    return seq


def test_long_entry_then_time_exit():
    eng = TrafficJamLongEngine(TrafficJamLongConfig())     # proactive_stop_bars=10
    seq = _ranging_then_three_down_closes() + [100.0] * 15  # hold flat -> time exit
    signals = []
    for c in seq:
        sig, reason = eng.update(c, c + 0.3, c - 0.3, c, 1.0)
        if sig != "HOLD":
            signals.append((sig, reason))
    assert ("BUY", "enter_long") in signals
    assert ("SELL", "exit_time_stop") in signals
    assert eng.position == 0


def test_long_entry_then_protective_stop():
    eng = TrafficJamLongEngine(TrafficJamLongConfig())
    events = [(c, c + 0.3, c - 0.3, c) for c in _ranging_then_three_down_closes()]
    # right after entry, a bar whose low blows through the protective stop.
    events += [(99.0, 99.5, 80.0, 99.0)] + [(99.0, 99.3, 98.7, 99.0)] * 5
    signals = []
    for o, h, l, c in events:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            signals.append((sig, reason))
    assert ("BUY", "enter_long") in signals
    assert ("SELL", "exit_protect_stop") in signals
    assert eng.position == 0


def test_entry_requires_volume():
    eng = TrafficJamLongEngine(TrafficJamLongConfig())
    seq = _ranging_then_three_down_closes() + [100.0] * 5
    signals = [eng.update(c, c + 0.3, c - 0.3, c, 0.0)[0] for c in seq]  # Vol == 0
    assert all(s == "HOLD" for s in signals)
    assert eng.position == 0
