"""Focused tests for the Three EMA Crossover short engine.

Pure Python; no Nautilus, no network. Crafted OHLCV paths (with small EMA periods
so crossovers warm up quickly) exercise the triple-EMA short entry, the two exits
(EMA reversal and the ratcheting range trailing stop), and the volume / trend
guards. Runnable via ``pytest tests_platform -k three_ema``.
"""
from __future__ import annotations

from strategies.three_ema_crossover_short.config import ThreeEmaCrossoverShortConfig as Cfg
from strategies.three_ema_crossover_short.engine import ThreeEmaCrossoverShortEngine as Engine

# Small EMA periods keep the warmup short and the crossovers deterministic.
_KW = dict(avg_len1=2, avg_len2=3, avg_len3=4, r_length=2)


def _signals(eng, bars, volume=1.0):
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, volume)
        if sig != "HOLD":
            out.append((sig, reason))
    return out


def _uptrend(n=12, start=100.0, step=2.0, half=0.3):
    return [(start + i * step, start + i * step + half, start + i * step - half, start + i * step)
            for i in range(n)]


def _downtrend(n, start, step=3.0, half=0.5):
    return [(start - i * step, start - i * step + half, start - i * step - half, start - i * step)
            for i in range(n)]


# -- entry ------------------------------------------------------------------

def test_entry_short_on_crossunder_with_mid_below_slow():
    # Rise (fast>mid>slow), then a sustained decline: fast EMA crosses under the
    # mid EMA while the mid EMA has fallen below the slow EMA -> short.
    bars = _uptrend() + _downtrend(20, start=124.0)
    eng = Engine(Cfg(**_KW))
    sigs = _signals(eng, bars)
    assert ("SELL", "enter_short") in sigs


def test_entry_requires_volume():
    bars = _uptrend() + _downtrend(20, start=124.0)
    eng = Engine(Cfg(**_KW))
    sigs = _signals(eng, bars, volume=0.0)  # Vol == 0 gates every order
    assert sigs == []
    assert eng.position == 0


def test_no_short_in_uptrend():
    # A steady uptrend keeps fast>mid>slow: no crossunder -> never shorts.
    eng = Engine(Cfg(**_KW))
    sigs = _signals(eng, _uptrend(n=40, step=1.0))
    assert all(s != "SELL" for s, _ in sigs)
    assert eng.position == 0


# -- exits ------------------------------------------------------------------

def test_trailing_stop_exit():
    # After the short, a single upward high spike lifts High to the ratcheting
    # ShortStopPrice[1] before the EMAs reverse -> trailing-stop cover.
    down = _downtrend(20, start=124.0)
    bars = _uptrend() + down[:5] + [(70, 90, 69, 71)] + down[5:]
    sigs = _signals(Engine(Cfg(**_KW)), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_trailing_stop") in sigs


def test_reversal_exit():
    # Wide-range short bars push the trailing stop far above price; a sharp V-up
    # then flips the fast EMA over the mid EMA first -> EMA-reversal cover.
    bars = _uptrend(half=0.3)
    bars += _downtrend(14, start=124.0, half=8.0)     # wide ranges -> distant stop
    bars += [(82 + i * 4, 82 + i * 4 + 0.3, 82 + i * 4 - 0.3, 82 + i * 4) for i in range(10)]
    sigs = _signals(Engine(Cfg(**_KW)), bars)
    assert ("SELL", "enter_short") in sigs
    assert ("BUY", "exit_reversal") in sigs
