#!/usr/bin/env python3
"""Read-only **dry-run** validator for a strategy backtest config.

Mirrors the *parsing/preparation* steps of ``run_strategy.py`` - config parse,
registry lookup, config-object build, feature-spec build, strategy resolution,
data-section validation, and a **bounded per-date** data load - **without**
running the strategy loop, calling ``run_strategy.py``, building an execution
backend, or entering the Nautilus backtest engine.

    python scripts/dry_run_strategy_config.py \\
        --config configs/backtests/vwm_short_btcusdt_1m_dryrun.yaml

It is strictly read-only: no backtest, no orders, no parquet/manifest writes,
no network, no Nautilus instantiation. The execution/backend section is only
*parsed and reported*, never built.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.loader import load_events  # noqa: E402
from scripts.run_bar_loader_smoke import (  # noqa: E402
    date_range,
    load_range_per_date,
    ns_to_iso,
    parse_date,
)

_REQUIRED_FILTER_KEYS = (
    "asset_class", "exchange", "venue_type", "symbol", "data_type", "freq",
)


def load_config(path: str | Path) -> dict:
    """Parse a strategy YAML config into a dict."""
    import yaml  # lazy: only needed when actually parsing a file

    # Read as UTF-8 explicitly: the platform default (e.g. gbk/cp936 on Windows)
    # would fail on any non-ASCII byte in the config.
    text = Path(path).read_text(encoding="utf-8")
    cfg = yaml.safe_load(text) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"config {str(path)!r} did not parse to a mapping")
    return cfg


def validate_data_section(data_cfg: dict) -> None:
    """Validate the ``data:`` section for a hive_parquet_bars dry-run."""
    if not data_cfg:
        raise ValueError("config has no 'data:' section")
    mode = data_cfg.get("mode")
    if mode != "hive_parquet_bars":
        raise ValueError(f"dry-run expects data.mode 'hive_parquet_bars', got {mode!r}")
    if not data_cfg.get("root"):
        raise ValueError("data.root is required")
    filters = data_cfg.get("filters") or {}
    missing = [k for k in _REQUIRED_FILTER_KEYS if k not in filters]
    if missing:
        raise ValueError(f"data.filters missing required keys: {missing}")
    if "start" not in data_cfg or "end" not in data_cfg:
        raise ValueError("dry-run requires data.start and data.end (small window)")


def build_config_obj(config_cls: type, params: dict):
    """Instantiate a config dataclass, ignoring params it doesn't declare
    (mirrors ``run_strategy._build_config_obj``)."""
    allowed = {f.name for f in fields(config_cls)}
    return config_cls(**{k: v for k, v in params.items() if k in allowed})


def _resolve_root(repo_root: Path, root: str) -> str:
    p = Path(root)
    return str(p if p.is_absolute() else repo_root / p)


def dry_run(
    config_path: str | Path,
    *,
    repo_root: Path = _REPO,
    get_plugin=None,
    load_fn=load_events,
    instantiate_strategy: bool = True,
) -> dict:
    """Parse + prepare a strategy config and bounded-load its data; never run it.

    ``get_plugin`` / ``load_fn`` are injectable so unit tests can run fully
    offline (no pandas/pyarrow, no real registry). Returns a result dict.
    """
    if get_plugin is None:  # default: the real registry (lazy import keeps tests offline)
        from strategy_framework.registry import get_entry as get_plugin  # noqa: PLC0415

    cfg = load_config(config_path)
    name = cfg.get("strategy")
    if not name:
        raise ValueError("config has no 'strategy:' key")

    plugin = get_plugin(name)                      # registry lookup
    config_obj = build_config_obj(plugin.config_cls, cfg.get("params", {}))
    specs = plugin.build_specs(config_obj)
    spec_names = [s.name for s in specs]

    strategy_resolved = False
    if instantiate_strategy:
        plugin.strategy_cls(config_obj)            # resolve strategy (no engine)
        strategy_resolved = True

    data_cfg = dict(cfg.get("data", {}))
    validate_data_section(data_cfg)
    data_cfg["root"] = _resolve_root(repo_root, data_cfg["root"])

    start, end = parse_date(str(data_cfg["start"])), parse_date(str(data_cfg["end"]))
    dates = date_range(start, end)
    events, days_loaded, missing_days = load_range_per_date(data_cfg, dates, load_fn=load_fn)

    # bounded integrity stats over the loaded window
    monotonic, duplicate_ts, prev = True, 0, None
    for e in events:
        ts = e.event_time_ns
        if prev is not None:
            if ts < prev:
                monotonic = False
            if ts == prev:
                duplicate_ts += 1
        prev = ts

    execution = cfg.get("execution", {}) or {}

    return {
        "config_path": str(config_path),
        "strategy_name": name,
        "data_mode": data_cfg.get("mode"),
        "data_root": data_cfg.get("root"),
        "filters": data_cfg.get("filters"),
        "date_start": str(data_cfg["start"]),
        "date_end": str(data_cfg["end"]),
        "days_requested": len(dates),
        "days_loaded": len(days_loaded),
        "missing_days": missing_days,
        "loaded_event_count": len(events),
        "first_ts_ns": events[0].event_time_ns if events else None,
        "last_ts_ns": events[-1].event_time_ns if events else None,
        "monotonic": monotonic,
        "duplicate_ts": duplicate_ts,
        "spec_names": spec_names,
        "feature_specs_count": len(specs),
        "registry_lookup_ok": True,
        "strategy_resolved": strategy_resolved,
        "backend_type": execution.get("backend"),
        "backend_mode": execution.get("mode"),
        # Hard guarantees: this script never does any of these.
        "ran_backtest": False,
        "called_run_strategy": False,
        "entered_nautilus_engine": False,
    }


def _print_report(r: dict) -> None:
    print(f"CONFIG_PATH: {r['config_path']}")
    print(f"STRATEGY_NAME: {r['strategy_name']}")
    print(f"REGISTRY_LOOKUP_OK: {r['registry_lookup_ok']}")
    print(f"STRATEGY_RESOLVED: {r['strategy_resolved']}")
    print(f"DATA_MODE: {r['data_mode']}")
    print(f"DATA_ROOT: {r['data_root']}")
    print(f"FILTERS: {r['filters']}")
    print(f"DATE_RANGE: {r['date_start']} .. {r['date_end']} "
          f"(days_requested={r['days_requested']}, days_loaded={r['days_loaded']})")
    print(f"MISSING_DAYS({len(r['missing_days'])}): {r['missing_days']}")
    print(f"LOADED_EVENT_COUNT: {r['loaded_event_count']}")
    print(f"FIRST_TS: {ns_to_iso(r['first_ts_ns']) if r['first_ts_ns'] else 'none'} ({r['first_ts_ns']})")
    print(f"LAST_TS: {ns_to_iso(r['last_ts_ns']) if r['last_ts_ns'] else 'none'} ({r['last_ts_ns']})")
    print(f"MONOTONIC_TS: {r['monotonic']}")
    print(f"DUPLICATE_TS_COUNT: {r['duplicate_ts']}")
    print(f"FEATURE_SPECS_COUNT: {r['feature_specs_count']}  names={r['spec_names']}")
    print(f"BACKEND_TYPE: {r['backend_type']}")
    print(f"BACKEND_MODE: {r['backend_mode']}")
    print(f"RAN_BACKTEST: {r['ran_backtest']}")
    print(f"CALLED_RUN_STRATEGY: {r['called_run_strategy']}")
    print(f"ENTERED_NAUTILUS_BACKTEST_ENGINE: {r['entered_nautilus_engine']}")
    print("DRY_RUN_DONE")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only strategy config dry-run (no backtest)")
    ap.add_argument("--config", required=True, help="path to a strategy YAML config")
    args = ap.parse_args(argv)
    _print_report(dry_run(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
