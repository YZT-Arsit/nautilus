"""Unit tests for the read-only strategy config dry-run (Strategy Backtest Prep).

Fully offline: a fake plugin and a fake loader replace the real registry and
data layer, so no pandas, no pyarrow, no real data, no Nautilus, no backtest.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.events import BarEvent
from scripts.dry_run_strategy_config import (
    build_config_obj,
    dry_run,
    validate_data_section,
)

_DAY_NS = 86_400_000_000_000
_MIN_NS = 60_000_000_000
_D0 = int(date(2026, 6, 14).toordinal() - date(1970, 1, 1).toordinal()) * _DAY_NS


# --- fakes -----------------------------------------------------------------

@dataclass
class _FakeConfig:
    mom_len: int = 5
    bar_type: str | None = None


@dataclass
class _Spec:
    name: str


class _FakeStrategy:
    def __init__(self, config):
        self.config = config


class _FakePlugin:
    name = "vwm_short"
    config_cls = _FakeConfig
    strategy_cls = _FakeStrategy

    @staticmethod
    def build_specs(config):
        return [_Spec("ohlcv.close"), _Spec("ohlcv.high"),
                _Spec("ohlcv.low"), _Spec("ohlcv.volume")]


def _bar(ts_ns):
    return BarEvent(close=100.0, open=100.0, high=100.0, low=100.0, volume=1.0,
                    instrument_id="BTCUSDT.BINANCE", event_time_ns=ts_ns)


def _make_get_plugin(plugin=None):
    plugin = plugin or _FakePlugin()
    def get_plugin(name):
        if name != plugin.name:
            raise KeyError(name)
        return plugin
    return get_plugin


def _make_load_fn(per_date, *, missing=()):
    """Fake load_events: returns ([], live) for the date in cfg['filters']['date']."""
    def load_fn(cfg):
        d = cfg["filters"]["date"]
        if d in missing:
            raise ValueError(f"no parquet fragments under {cfg['root']!r} match filters")
        return [], list(per_date.get(d, []))
    return load_fn


_BASE_CFG = {
    "strategy": "vwm_short",
    "params": {"mom_len": 5, "bar_type": "1m", "unknown_param": 99},
    "data": {
        "mode": "hive_parquet_bars",
        "root": "historical_data/market_data",
        "filters": {"exchange": "BINANCE", "venue_type": "spot",
                    "symbol": "BTCUSDT", "bar_type": "1m"},
        "start": "2026-06-14", "end": "2026-06-16",
    },
    "execution": {"backend": "nautilus_backtest", "mode": "nautilus_native"},
}


def _write_yaml_like(monkeypatch, cfg: dict):
    """Patch load_config to return ``cfg`` (avoids needing PyYAML offline)."""
    import scripts.dry_run_strategy_config as mod
    monkeypatch.setattr(mod, "load_config", lambda path: cfg)


# --- validate_data_section -------------------------------------------------

def test_validate_data_section_ok():
    validate_data_section(_BASE_CFG["data"])  # no raise


def test_validate_data_section_wrong_mode():
    with pytest.raises(ValueError, match="hive_parquet_bars"):
        validate_data_section({**_BASE_CFG["data"], "mode": "synthetic"})


def test_validate_data_section_missing_root():
    bad = dict(_BASE_CFG["data"]); bad.pop("root")
    with pytest.raises(ValueError, match="data.root"):
        validate_data_section(bad)


def test_validate_data_section_missing_filter_key():
    bad = {**_BASE_CFG["data"], "filters": {"exchange": "BINANCE"}}
    with pytest.raises(ValueError, match="missing required keys"):
        validate_data_section(bad)


def test_validate_data_section_missing_window():
    bad = dict(_BASE_CFG["data"]); bad.pop("start")
    with pytest.raises(ValueError, match="start and data.end"):
        validate_data_section(bad)


# --- build_config_obj ------------------------------------------------------

def test_build_config_obj_ignores_unknown_params():
    obj = build_config_obj(_FakeConfig, {"mom_len": 7, "bar_type": "1m", "nope": 1})
    assert obj.mom_len == 7 and obj.bar_type == "1m"


# --- dry_run (fully faked) -------------------------------------------------

def test_dry_run_happy_path(monkeypatch):
    _write_yaml_like(monkeypatch, _BASE_CFG)
    per_date = {
        "2026-06-14": [_bar(_D0 + i * _MIN_NS) for i in range(3)],
        "2026-06-15": [_bar(_D0 + _DAY_NS + i * _MIN_NS) for i in range(3)],
        "2026-06-16": [_bar(_D0 + 2 * _DAY_NS + i * _MIN_NS) for i in range(3)],
    }
    r = dry_run("ignored.yaml", get_plugin=_make_get_plugin(),
                load_fn=_make_load_fn(per_date))
    assert r["strategy_name"] == "vwm_short"
    assert r["registry_lookup_ok"] is True
    assert r["strategy_resolved"] is True
    assert r["data_mode"] == "hive_parquet_bars"
    assert r["filters"]["bar_type"] == "1m"
    assert r["days_requested"] == 3 and r["days_loaded"] == 3 and r["missing_days"] == []
    assert r["loaded_event_count"] == 9
    assert r["first_ts_ns"] == _D0
    assert r["monotonic"] is True and r["duplicate_ts"] == 0
    assert r["feature_specs_count"] == 4
    assert r["spec_names"] == ["ohlcv.close", "ohlcv.high", "ohlcv.low", "ohlcv.volume"]
    assert r["backend_type"] == "nautilus_backtest" and r["backend_mode"] == "nautilus_native"


def test_dry_run_never_runs_backtest_or_nautilus(monkeypatch):
    _write_yaml_like(monkeypatch, _BASE_CFG)
    r = dry_run("ignored.yaml", get_plugin=_make_get_plugin(),
                load_fn=_make_load_fn({}))
    assert r["ran_backtest"] is False
    assert r["called_run_strategy"] is False
    assert r["entered_nautilus_engine"] is False


def test_dry_run_reports_missing_day(monkeypatch):
    _write_yaml_like(monkeypatch, _BASE_CFG)
    per_date = {"2026-06-14": [_bar(_D0)], "2026-06-16": [_bar(_D0 + 2 * _DAY_NS)]}
    r = dry_run("ignored.yaml", get_plugin=_make_get_plugin(),
                load_fn=_make_load_fn(per_date, missing=["2026-06-15"]))
    assert r["days_loaded"] == 2
    assert r["missing_days"] == ["2026-06-15"]
    assert r["loaded_event_count"] == 2


def test_dry_run_can_skip_strategy_instantiation(monkeypatch):
    _write_yaml_like(monkeypatch, _BASE_CFG)
    r = dry_run("ignored.yaml", get_plugin=_make_get_plugin(),
                load_fn=_make_load_fn({}), instantiate_strategy=False)
    assert r["strategy_resolved"] is False
    assert r["registry_lookup_ok"] is True  # lookup still happened


def test_dry_run_unknown_strategy_raises(monkeypatch):
    _write_yaml_like(monkeypatch, {**_BASE_CFG, "strategy": "does_not_exist"})
    with pytest.raises(KeyError):
        dry_run("ignored.yaml", get_plugin=_make_get_plugin(), load_fn=_make_load_fn({}))


# --- load_config (needs PyYAML; server has it) -----------------------------

def test_load_config_parses_yaml(tmp_path):
    pytest.importorskip("yaml")
    from scripts.dry_run_strategy_config import load_config
    p = tmp_path / "c.yaml"
    p.write_text("strategy: vwm_short\nparams: {mom_len: 5}\n")
    cfg = load_config(p)
    assert cfg["strategy"] == "vwm_short" and cfg["params"]["mom_len"] == 5


# --- source scan -----------------------------------------------------------

def test_source_scan_no_network_nautilus_or_trading():
    import inspect

    from scripts import dry_run_strategy_config

    src = inspect.getsource(dry_run_strategy_config)
    # the dry-run script must not import nautilus, run_strategy, or build a backend
    assert "import nautilus_trader" not in src
    assert "from nautilus_trader" not in src
    assert "import run_strategy" not in src
    assert "build_backend" not in src
    for net in ("import websocket", "import websockets", "import asyncio",
                "import aiohttp", "import urllib", "import requests", "import socket"):
        assert net not in src, f"unexpected network import: {net}"
    for forbidden in ("api_key", "apiKey", "secret", "signature", "place_order",
                      "new_order", "cancel_order", "/api/v3/order", "/sapi/"):
        assert forbidden not in src, f"unexpected trading reference: {forbidden}"
