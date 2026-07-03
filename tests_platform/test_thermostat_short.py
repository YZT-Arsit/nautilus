"""Focused tests for the Thermostat short engine (regime-switching, offline).

Pure Python; no Nautilus, no network. Because the regime (swing vs trend) is
chosen by the CMI, the tests pin the regime by overriding ``swing_trend_switch``
(``100`` -> always swing, ``0`` -> always trend) so each regime's entry/exit
branches can be exercised deterministically; a third test uses the default
switch (20) to drive a swing-entered position into the trend regime and out via
the ATR protective stop. Runnable via ``pytest tests_platform -k thermostat``.
"""
from __future__ import annotations

import math
from collections import Counter

from strategies.thermostat_short.config import ThermostatShortConfig as Cfg
from strategies.thermostat_short.engine import ThermostatShortEngine as Engine


def _reasons(bars, **kw):
    eng = Engine(Cfg(**kw))
    out = []
    for o, h, l, c in bars:
        sig, reason = eng.update(o, h, l, c, 1.0)
        if sig != "HOLD":
            out.append(reason)
    return Counter(out), eng


def _chop(n, mid=100.0, amp=3.0):
    """Deterministic ranging path (small directional drift -> low CMI = swing)."""
    return [(mid + amp * math.sin((i - 1) / 2.0),
             max(mid + amp * math.sin((i - 1) / 2.0), mid + amp * math.sin(i / 2.0)) + 0.5,
             min(mid + amp * math.sin((i - 1) / 2.0), mid + amp * math.sin(i / 2.0)) - 0.5,
             mid + amp * math.sin(i / 2.0)) for i in range(n)]


def _noise(n, mid=100.0, amp=1.0):
    return [(mid + amp * math.sin((i - 1) / 1.7),
             mid + amp * math.sin(i / 1.7) + 0.6,
             mid + amp * math.sin(i / 1.7) - 0.6,
             mid + amp * math.sin(i / 1.7)) for i in range(n)]


# -- swing regime (opening-range ATR breakout) ------------------------------

def test_swing_regime_entry_and_cover():
    # swing_trend_switch=100 -> CMI always below it -> always the swing regime.
    reasons, _ = _reasons(_chop(80), swing_trend_switch=100.0)
    assert reasons["swing_enter_short"] >= 1
    assert reasons["swing_cover"] >= 1
    # no trend-regime branches should fire when the regime is pinned to swing.
    assert reasons["trend_enter_short"] == 0
    assert reasons["trend_cover"] == 0


# -- trend regime (Bollinger breakout) --------------------------------------

def test_trend_regime_entry_and_cover():
    # swing_trend_switch=0 -> CMI always >= it -> always the trend regime.
    bars = _noise(55)
    bars += [(100 - i * 2.5, 100 - i * 2.5 + 0.4, 100 - i * 2.5 - 1.0, 100 - i * 2.5 - 0.9)
             for i in range(16)]                                  # break the lower band
    bars += [(62 + i * 5, 62 + i * 5 + 1.5, 62 + i * 5 - 0.5, 62 + i * 5 + 1.3)
             for i in range(25)]                                  # bounce up to the MA / upper band
    reasons, _ = _reasons(bars, swing_trend_switch=0.0)
    assert reasons["trend_enter_short"] >= 1
    assert reasons["trend_cover"] >= 1
    assert reasons["swing_enter_short"] == 0


# -- swing position exits via the ATR protective stop in the trend regime ----

def test_swing_position_atr_protective_stop_in_trend():
    # Default switch (20): a swing short taken as the decline begins is carried
    # into the trend regime, then stopped out on the bounce by High >= Entry+3*ATR.
    bars = _chop(60)
    bars += [(100 - i * 2, 100 - i * 2 + 0.5, 100 - i * 2 - 1.0, 100 - i * 2 - 0.8)
             for i in range(25)]                                  # decline (CMI climbs into trend)
    bars += [(52 + i * 3, 52 + i * 3 + 1.0, 52 + i * 3 - 0.5, 52 + i * 3 + 0.8)
             for i in range(30)]                                  # bounce trips the ATR stop
    reasons, _ = _reasons(bars)
    assert reasons["swing_enter_short"] >= 1
    assert reasons["swing_prot_stop_cover"] >= 1


# -- warmup guard -----------------------------------------------------------

def test_no_trades_before_cmi_ready():
    # CMI needs 30 bars; nothing should fire before it (prev_cmi is None).
    reasons, eng = _reasons(_chop(29))
    assert sum(reasons.values()) == 0
    assert eng.position == 0
