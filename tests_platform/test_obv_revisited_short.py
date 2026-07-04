"""Focused tests for the OBV Revisited short engine.

Pure Python; no Nautilus, no network. WOBV is a running volatility-weighted OBV:
bullish bars (close>open) raise it, bearish bars (close<open) lower it. A rally
warms the WOBV above its MA, a bearish flip crosses the WOBV under the MA (arming
the short trigger at that bar's low), a lower bar breaks the trigger (short), and
a bullish resumption up-crosses the MA (cover). Runnable via ``pytest
tests_platform -k obv_revisited``.
"""
from __future__ import annotations

from strategies.obv_revisited_short.config import ObvRevisitedShortConfig as Cfg
from strategies.obv_revisited_short.engine import ObvRevisitedShortEngine as Engine


def _bull(p):
    return (p, p + 1.0, p - 0.3, p + 0.8)  # close > open -> WOBV up


def _bear(p):
    return (p, p + 0.3, p - 1.0, p - 0.8)  # close < open -> WOBV down


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _rally_dip_recover():
    bulls = [_bull(100 + i * 0.3) for i in range(30)]    # WOBV rises, MA warmed
    bears = [_bear(109 - i * 0.7) for i in range(10)]    # WOBV down-crosses -> arm short
    recover = [_bull(102 + i * 0.5) for i in range(12)]  # WOBV up-crosses -> cover
    return bulls + bears + recover


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg()), _rally_dip_recover())
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_cover") in sigs


def test_no_trades_on_zero_volume():
    # WOBV only moves when Vol > 0; a zero-volume stream never crosses -> no trades.
    eng = Engine(Cfg())
    sigs = _signals(eng, _rally_dip_recover(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_short_in_pure_rally():
    # Purely bullish bars keep WOBV above its lagging MA -> no down-cross -> no short.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bull(100 + i * 0.3) for i in range(60)])
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0
