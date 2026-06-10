#!/usr/bin/env python3
"""Shared strategy runner — one entry point for *every* strategy.

Strategy authors do **not** write a run script. To add a strategy you only:

1. add ``feature_strategies/strategies/<name>.py`` (config + ``build_specs`` + strategy class);
2. register it in ``feature_strategies/registry.py``;
3. add ``feature_strategies/configs/<name>.yaml`` to choose parameters.

Then run it through this shared script::

    python -m feature_strategies.run_strategy --config feature_strategies/configs/<name>.yaml
    python -m feature_strategies.run_strategy --strategy <name>

This file only *coordinates*: it loads config, looks up the registry, builds the
strategy + runner, gets events from :mod:`feature_strategies.data_loaders`, runs
the loop, and delegates display to :mod:`feature_strategies.output`. It contains
no data construction, no table formatting, and no event-shape assumptions.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from feature_strategies import output
from feature_strategies.backtest import SignalRecorder
from feature_strategies.data_loaders import load_events
from feature_strategies.registry import get_entry
from nautilus_ext.features.runner import FeatureStrategyRunner


def _load_config(args: argparse.Namespace) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if args.config:
        cfg = yaml.safe_load(Path(args.config).read_text()) or {}
    if args.strategy:
        cfg["strategy"] = args.strategy
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

    cfg = _load_config(args)
    name = cfg.get("strategy")
    if not name:
        parser.error("no strategy given: pass --strategy NAME or a --config with a 'strategy:' key")

    entry = get_entry(name)
    config_obj = _build_config_obj(entry.config_cls, cfg.get("params", {}))
    specs = entry.build_specs(config_obj)
    spec_names = [s.name for s in specs]
    runner = FeatureStrategyRunner(specs, entry.strategy_cls(config_obj))

    warmup_events, live_events = load_events(cfg.get("data", {}))
    runner.warmup(iter(warmup_events))
    output.print_warmup_summary(name, len(warmup_events), runner, spec_names)

    output_cfg = cfg.get("output", {})
    print_table = output_cfg.get("print_table", True)
    recorder = SignalRecorder(spec_names) if output_cfg.get("record_signals", False) else None

    if print_table:
        output.print_event_table_header(spec_names)
    for event, snapshot, signal in runner.run(live_events):
        if print_table:
            output.print_event_row(event, snapshot, signal, spec_names)
        if recorder is not None:
            recorder.record(event, snapshot, signal)

    if recorder is not None:
        output.print_signal_summary(recorder.signal_counts())


if __name__ == "__main__":
    main()
