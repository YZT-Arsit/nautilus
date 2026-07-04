"""Focused tests for the OBV Revisited long engine (mirror of short).

Pure Python; no Nautilus, no network. WOBV is a running volatility-weighted OBV:
bullish bars (close>open) raise it, bearish bars (close<open) lower it. A decline
pushes the WOBV below its MA, a rally up-crosses the MA (arming the long trigger
at that bar's high), a higher bar breaks the trigger (long), and a renewed
decline down-crosses the MA (sell). Runnable via ``pytest tests_platform -k
obv_revisited``.
"""
from __future__ import annotations

from strategies.obv_revisited_long.config import ObvRevisitedLongConfig as Cfg
from strategies.obv_revisited_long.engine import ObvRevisitedLongEngine as Engine


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


def _dip_rally_dip():
    warm = [_bull(100 + i * 0.3) for i in range(20)]
    down = [_bear(106 - i * 0.7) for i in range(12)]     # WOBV falls below MA
    up = [_bull(98 + i * 0.6) for i in range(10)]        # WOBV up-crosses -> arm long
    down2 = [_bear(105 - i * 0.7) for i in range(12)]    # WOBV down-crosses -> sell
    return warm + down + up + down2


def test_entry_and_exit():
    sigs = _signals(Engine(Cfg()), _dip_rally_dip())
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_sell") in sigs


def test_no_trades_on_zero_volume():
    # WOBV only moves when Vol > 0; a zero-volume stream never crosses -> no trades.
    eng = Engine(Cfg())
    sigs = _signals(eng, _dip_rally_dip(), volume=0.0)
    assert sigs == []
    assert eng.position == 0


def test_no_long_in_pure_decline():
    # Purely bearish bars keep WOBV below its lagging MA -> no up-cross -> no long.
    eng = Engine(Cfg())
    sigs = _signals(eng, [_bear(150 - i * 0.3) for i in range(60)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0
