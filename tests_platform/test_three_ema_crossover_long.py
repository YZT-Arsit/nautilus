"""Focused tests for the Three EMA Crossover long engine (mirror of short).

Pure Python; no Nautilus, no network. Crafted OHLCV paths (with small EMA periods
so crossovers warm up quickly) exercise the triple-EMA long entry, the two exits
(EMA reversal and the ratcheting range trailing stop), and the volume / trend
guards. Runnable via ``pytest tests_platform -k three_ema``.
"""
from __future__ import annotations

from strategies.three_ema_crossover_long.config import ThreeEmaCrossoverLongConfig as Cfg
from strategies.three_ema_crossover_long.engine import ThreeEmaCrossoverLongEngine as Engine

# Small EMA periods keep the warmup short and the crossovers deterministic.
_KW = dict(avg_len1=2, avg_len2=3, avg_len3=4, r_length=2)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _downtrend(n=12, start=124.0, step=2.0, half=0.3):
    return [(start - i * step, start - i * step + half, start - i * step - half, start - i * step)
            for i in range(n)]


def _uptrend(n, start, step=3.0, half=0.5):
    return [(start + i * step, start + i * step + half, start + i * step - half, start + i * step)
            for i in range(n)]


# -- entry ------------------------------------------------------------------

def test_entry_long_on_crossover_with_mid_above_slow():
    # Fall (fast<mid<slow), then a sustained rise: fast EMA crosses over the mid
    # EMA while the mid EMA has risen above the slow EMA -> long.
    bars = _downtrend() + _uptrend(20, start=100.0)
    eng = Engine(Cfg(**_KW))
    sigs = _signals(eng, bars)
    assert ("BUY", "enter_long") in sigs


def test_entry_requires_volume():
    bars = _downtrend() + _uptrend(20, start=100.0)
    eng = Engine(Cfg(**_KW))
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_long_in_downtrend():
    # A steady downtrend keeps fast<mid<slow: no crossover -> never longs.
    eng = Engine(Cfg(**_KW))
    sigs = _signals(eng, [(200 - i, 200 - i + 0.3, 200 - i - 0.3, 200 - i) for i in range(40)])
    assert all(s != "BUY" for s, _ in sigs)
    assert eng.position == 0


# -- exits ------------------------------------------------------------------

def test_trailing_stop_exit():
    # After the long, a single downward low spike drops Low to the ratcheting
    # LongStopPrice[1] before the EMAs reverse -> trailing-stop exit.
    up = _uptrend(20, start=100.0)
    bars = _downtrend() + up[:5] + [(120, 121, 100, 119)] + up[5:]
    sigs = _signals(Engine(Cfg(**_KW)), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_trailing_stop") in sigs


def test_reversal_exit():
    # Wide-range long bars push the trailing stop far below price; a sharp V-down
    # then flips the fast EMA under the mid EMA first -> EMA-reversal exit.
    bars = _downtrend(half=0.3)
    bars += _uptrend(14, start=100.0, half=8.0)       # wide ranges -> distant stop
    bars += [(142 - i * 4, 142 - i * 4 + 0.3, 142 - i * 4 - 0.3, 142 - i * 4) for i in range(10)]
    sigs = _signals(Engine(Cfg(**_KW)), bars)
    assert ("BUY", "enter_long") in sigs
    assert ("SELL", "exit_reversal") in sigs
