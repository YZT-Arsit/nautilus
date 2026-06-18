"""Tests for the trend-breakout + ATR strategy (offline, no Nautilus/pandas).

The decision engine is pure Python, so we drive it bar-by-bar with small windows
and assert signals/reasons, position semantics, cooldown, and no look-ahead.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from strategies.trend_breakout_atr.strategy import (
    BUY,
    HOLD,
    SELL,
    TrendBreakoutAtrConfig,
    TrendBreakoutAtrEngine,
    TrendBreakoutAtrStrategy,
    build_specs,
)


def _cfg(**over):
    base = dict(trend_len=3, breakout_len=2, atr_len=2, atr_mult_stop=10.0,
               atr_mult_exit=10.0, cooldown_bars=2, min_atr_pct=0.0, allow_short=True)
    base.update(over)
    return TrendBreakoutAtrConfig(**base)


def _eng(**over):
    return TrendBreakoutAtrEngine(_cfg(**over))


# --- A. warmup / readiness --------------------------------------------------

def test_warmup_holds_until_ready():
    e = _eng()
    # bars 1,2 cannot be ready (need 2 prior highs + 3 closes).
    assert e.update(100, 100, 100) == (HOLD, "warmup_hold")
    assert e.update(100, 100, 100) == (HOLD, "warmup_hold")
    # bar 3 is the first bar with full windows -> a real (here: no_signal) decision.
    sig, reason = e.update(100, 100, 100)
    assert reason != "warmup_hold"
    assert e.position == 0


# --- A. entries -------------------------------------------------------------

def test_flat_upward_breakout_opens_long():
    e = _eng()
    e.update(100, 100, 100); e.update(100, 100, 100)
    sig, reason = e.update(110, 110, 100)   # close 110 > prev_upper 100 and > trend_ma
    assert (sig, reason) == (BUY, "long_breakout")
    assert e.position == 1 and e.entry_price == 110


def test_flat_downward_breakout_opens_short():
    e = _eng()
    e.update(100, 100, 100); e.update(100, 100, 100)
    sig, reason = e.update(90, 100, 90)     # close 90 < prev_lower 100 and < trend_ma
    assert (sig, reason) == (SELL, "short_breakout")
    assert e.position == -1 and e.entry_price == 90


def test_low_volatility_blocks_entry():
    e = _eng(min_atr_pct=0.5)               # require ATR/close >= 50% (never true here)
    e.update(100, 100, 100); e.update(100, 100, 100)
    sig, reason = e.update(110, 110, 100)
    assert sig == HOLD and reason == "low_volatility_hold"
    assert e.position == 0


# --- A. no look-ahead -------------------------------------------------------

def test_no_lookahead_current_high_does_not_trigger_self():
    e = _eng()
    e.update(100, 105, 95); e.update(100, 105, 95)   # prev_upper=105, prev_lower=95
    # current high 120 is a new max, but close 104 stays inside the prior band
    # (not > prev_upper 105, not < prev_lower 95) -> the bar's own high must not
    # trigger its own breakout.
    sig, reason = e.update(104, 120, 95)
    assert sig == HOLD and reason == "no_signal"
    assert e.position == 0


# --- A. exits ---------------------------------------------------------------

def test_long_exit_on_trend_break():
    e = _eng()
    e.update(100, 100, 100); e.update(100, 100, 100)
    assert e.update(110, 110, 100)[0] == BUY        # open long
    sig, reason = e.update(95, 110, 95)             # close 95 < trend_ma -> exit
    assert sig == SELL and reason.startswith("long_exit")
    assert e.position == 0 and e.cooldown_remaining == 2


def test_short_exit_on_trend_break():
    e = _eng()
    e.update(100, 100, 100); e.update(100, 100, 100)
    assert e.update(90, 100, 90)[0] == SELL         # open short
    sig, reason = e.update(105, 105, 90)            # close 105 > trend_ma -> exit
    assert sig == BUY and reason.startswith("short_exit")
    assert e.position == 0 and e.cooldown_remaining == 2


def test_long_exit_stop_takes_priority():
    e = _eng(atr_mult_stop=0.5, atr_mult_exit=10.0)
    e.update(100, 100, 100); e.update(100, 100, 100)
    e.update(110, 110, 100)                          # long, entry 110, atr ~ (10,?)
    sig, reason = e.update(80, 110, 80)             # deep drop -> stop before trend
    assert sig == SELL and reason == "long_exit_stop"


# --- A/B. cooldown blocks re-entry -----------------------------------------

def test_cooldown_blocks_new_entry():
    e = _eng(cooldown_bars=2)
    e.update(100, 100, 100); e.update(100, 100, 100)
    e.update(110, 110, 100)                          # open long
    assert e.update(95, 110, 95)[0] == SELL         # close -> cooldown=2
    # next 2 bars are cooldown_hold even with a big breakout
    assert e.update(300, 300, 300) == (HOLD, "cooldown_hold")
    assert e.update(300, 300, 300) == (HOLD, "cooldown_hold")
    # cooldown elapsed -> a breakout can open again
    sig, _ = e.update(400, 400, 300)
    assert sig == BUY and e.position == 1


# --- B. position semantics --------------------------------------------------

def test_position_always_within_unit_and_never_reverses():
    e = _eng()
    seq = [(100,100,100),(100,100,100),(110,110,100),(95,110,95),(300,300,300),
           (300,300,300),(80,300,80),(50,80,50),(200,200,50)]
    prev = 0
    for c, h, l in seq:
        sig, reason = e.update(c, h, l)
        assert e.position in (-1, 0, 1)             # never exceeds +/-1
        # a single bar never flips long<->short directly (must pass through flat)
        assert not (prev == 1 and e.position == -1)
        assert not (prev == -1 and e.position == 1)
        prev = e.position


def test_ambiguous_inverted_band_holds():
    e = _eng()
    # directly exercise the defensive guard with an inverted band (corrupt input)
    sig, reason = e._decide(close=150, prev_upper=100, prev_lower=200,
                            trend_ma=150, atr=1.0)
    assert sig == HOLD and reason == "ambiguous_hold"


# --- B. on_snapshot adapter -------------------------------------------------

class _Snap:
    def __init__(self, close, high, low, ts=0, iid="BTCUSDT.BINANCE"):
        self._v = {"tba_bar_close": close, "tba_bar_high": high, "tba_bar_low": low}
        self.ts_event = ts
        self.instrument_id = iid

    def value(self, name):
        return self._v.get(name)


def test_on_snapshot_drives_engine_and_records_reason():
    s = TrendBreakoutAtrStrategy(_cfg())
    assert s.on_snapshot(_Snap(100, 100, 100)) == HOLD
    s.on_snapshot(_Snap(100, 100, 100))
    sig = s.on_snapshot(_Snap(110, 110, 100))
    assert sig == BUY and s.last_reason == "long_breakout" and s.position == 1


def test_on_snapshot_missing_value_holds():
    s = TrendBreakoutAtrStrategy(_cfg())
    assert s.on_snapshot(_Snap(None, 100, 100)) == HOLD
    assert s.last_reason == "warmup_hold"


# --- C. registry / config / specs ------------------------------------------

def test_registry_lookup():
    from strategy_framework.registry import get_entry
    plugin = get_entry("trend_breakout_atr")
    assert plugin.name == "trend_breakout_atr"
    assert plugin.config_cls is TrendBreakoutAtrConfig


def test_build_specs_three_passthrough():
    specs = build_specs(TrendBreakoutAtrConfig())
    assert [s.name for s in specs] == ["tba_bar_close", "tba_bar_high", "tba_bar_low"]


def test_default_config_dataclass():
    c = TrendBreakoutAtrConfig()
    assert c.trend_len == 120 and c.breakout_len == 60 and c.atr_len == 30
    assert c.cooldown_bars == 30 and c.allow_short is True


# --- D. source scan ---------------------------------------------------------

def test_source_scan_no_nautilus_network_or_order():
    import inspect

    from strategies.trend_breakout_atr import strategy as mod

    src = inspect.getsource(mod)
    assert "import nautilus_trader" not in src
    assert "from nautilus_trader" not in src
    for net in ("import websocket", "import asyncio", "import aiohttp",
                "import urllib", "import requests", "import socket"):
        assert net not in src, net
    for forbidden in ("api_key", "apiKey", "secret", "signature", "place_order",
                      "new_order", "cancel_order", "/api/v3/order", "/sapi/"):
        assert forbidden not in src, forbidden
