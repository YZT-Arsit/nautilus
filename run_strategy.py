#!/usr/bin/env python3
"""Top-level user entry point — run any registered strategy.

This is the only normal execution entry. It is strategy-agnostic: it loads a
config, looks up the strategy plugin in the registry, builds the runner, gets
events from the data loaders, runs the loop, and delegates display to the output
module. It contains no strategy-specific signal logic, no data construction, and
no table formatting.

Usage::

    python run_strategy.py --strategy ma_crossover
    python run_strategy.py --config strategies/ma_crossover/config.yaml
    python -m run_strategy --strategy ma_crossover

To add a strategy: create ``strategies/<name>/strategy.py`` (+ ``config.yaml``,
``README.md``) and register its ``PLUGIN`` in ``strategy_framework/registry.py``.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from data_engine.loader import load_events
from strategy_framework import output
from strategy_framework.backends import build_backend
from strategy_framework.backtest import SignalRecorder
from strategy_framework.registry import get_entry
from nautilus_ext.features.runner import FeatureStrategyRunner

# Repository root — relative config paths (e.g. a plugin's default_config_path)
# resolve against this so the runner works regardless of the caller's CWD.
_REPO_ROOT = Path(__file__).resolve().parent


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _REPO_ROOT / p


def _load_config(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve config from --config and/or --strategy (see module docstring)."""
    cfg: dict[str, Any] = {}
    config_path = args.config
    if config_path is None and args.strategy:
        # --strategy only: load the plugin's default config if it has one.
        plugin = get_entry(args.strategy)
        config_path = plugin.default_config_path
    if config_path:
        cfg = yaml.safe_load(_resolve(config_path).read_text()) or {}
    if args.strategy:
        cfg["strategy"] = args.strategy  # --strategy overrides cfg["strategy"]
    return cfg


def _build_config_obj(config_cls: type, params: dict[str, Any]):
    """Instantiate a config dataclass, ignoring params it doesn't declare."""
    allowed = {f.name for f in fields(config_cls)}
    return config_cls(**{k: v for k, v in params.items() if k in allowed})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a registered feature strategy")
    parser.add_argument("--config", type=str, help="path to a strategy YAML config")
    parser.add_argument("--strategy", type=str, help="registered strategy name (overrides config)")
    args = parser.parse_args(argv)

    if not args.config and not args.strategy:
        parser.error("provide --strategy NAME and/or --config PATH")

    cfg = _load_config(args)
    name = cfg.get("strategy")
    if not name:
        parser.error("no strategy given: pass --strategy NAME or a --config with a 'strategy:' key")

    plugin = get_entry(name)
    config_obj = _build_config_obj(plugin.config_cls, cfg.get("params", {}))
    specs = plugin.build_specs(config_obj)
    spec_names = [s.name for s in specs]
    runner = FeatureStrategyRunner(specs, plugin.strategy_cls(config_obj))

    data_cfg = dict(cfg.get("data", {}))
    for location_key in ("path", "root"):
        # Resolve a relative data location (CSV path / Parquet root) against the
        # repo root so the runner works regardless of the caller's CWD.
        if location_key in data_cfg:
            data_cfg[location_key] = str(_resolve(data_cfg[location_key]))
    warmup_events, live_events = load_events(data_cfg)
    runner.warmup(iter(warmup_events))
    output.print_warmup_summary(name, len(warmup_events), runner, spec_names)

    output_cfg = cfg.get("output", {})
    print_table = output_cfg.get("print_table", True)
    recorder = SignalRecorder(spec_names) if output_cfg.get("record_signals", False) else None
    # Optional execution backend (see strategy_framework/backends/). None keeps
    # the legacy print/record-only behaviour.
    backend = build_backend(cfg.get("execution", {}), spec_names)

    if print_table:
        output.print_event_table_header(spec_names)
    for event, snapshot, signal in runner.run(live_events):
        if print_table:
            output.print_event_row(event, snapshot, signal, spec_names)
        if recorder is not None:
            recorder.record(event, snapshot, signal)
        if backend is not None:
            backend.on_signal(event, snapshot, signal)

    if recorder is not None:
        output.print_signal_summary(recorder.signal_counts())
    if backend is not None:
        backend.close()


if __name__ == "__main__":
    main()
