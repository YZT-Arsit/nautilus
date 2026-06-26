"""Tests for the VWM short trend regime filter (pure helpers + config wiring).

The filter is config-gated and default-off; these prove the pure gate logic, that
disabled == baseline (never blocks), and that the filter params propagate into
VwmShortConfig (ignoring unknown keys). No network, no order/private endpoint.
"""
from __future__ import annotations

import inspect

from strategies.vwm_short.strategy import (
    VwmShortConfig,
    should_block_short_entry,
    simple_moving_average,
    trend_gate,
)
from run_strategy import _build_config_obj


# --- pure SMA / gate --------------------------------------------------------

def test_simple_moving_average():
    assert simple_moving_average([1.0, 2.0, 3.0, 4.0], 2) == 3.5   # mean(3,4)
    assert simple_moving_average([1.0, 2.0], 4) is None            # too few
    assert simple_moving_average([1.0], 0) is None                 # bad length


def test_trend_gate_downtrend_allows_uptrend_blocks():
    down = [100.0, 80.0, 60.0, 40.0, 20.0]   # falling -> fast mean < slow mean
    up = [20.0, 40.0, 60.0, 80.0, 100.0]     # rising -> fast mean > slow mean
    assert trend_gate(down, fast_len=2, slow_len=4) is True        # downtrend -> short allowed
    assert trend_gate(up, fast_len=2, slow_len=4) is False         # uptrend -> blocked
    assert trend_gate([100.0], fast_len=2, slow_len=4) is None     # insufficient history


# --- block decision ---------------------------------------------------------

def test_should_block_disabled_is_baseline():
    # disabled -> never blocks, regardless of regime (bit-for-bit baseline)
    for closes in ([20.0, 40.0, 60.0, 80.0, 100.0], [100.0], []):
        assert should_block_short_entry(closes, enabled=False, fast_len=2, slow_len=4) is False


def test_should_block_enabled_by_regime():
    down = [100.0, 80.0, 60.0, 40.0, 20.0]
    up = [20.0, 40.0, 60.0, 80.0, 100.0]
    assert should_block_short_entry(down, enabled=True, fast_len=2, slow_len=4) is False  # allow
    assert should_block_short_entry(up, enabled=True, fast_len=2, slow_len=4) is True     # block
    # warmup (gate None) -> conservatively block
    assert should_block_short_entry([100.0], enabled=True, fast_len=2, slow_len=4) is True


# --- config wiring ----------------------------------------------------------

def test_default_config_filter_off():
    c = VwmShortConfig()
    assert c.enable_trend_filter is False
    assert c.trend_filter_fast_len == 96 and c.trend_filter_slow_len == 384
    assert c.trend_filter_mode == "short_only_downtrend"


def test_config_propagation_ignores_unknown_keys():
    params = {"mom_len": 5, "enable_trend_filter": True, "trend_filter_fast_len": 96,
              "trend_filter_slow_len": 384, "trend_filter_mode": "short_only_downtrend",
              "trend_filter_source": "close", "unknown_key": 123}
    cfg = _build_config_obj(VwmShortConfig, params)
    assert cfg.enable_trend_filter is True
    assert cfg.trend_filter_fast_len == 96 and cfg.trend_filter_slow_len == 384
    assert not hasattr(cfg, "unknown_key")


# --- safety -----------------------------------------------------------------

def test_filter_helpers_no_network_or_order():
    import strategies.vwm_short.strategy as mod
    src = inspect.getsource(mod)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "set_leverage", "websocket", "/order", "cancel_order"):
        assert banned not in src, banned
