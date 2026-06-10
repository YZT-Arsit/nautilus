#!/usr/bin/env python3
"""Shared strategy runner — one entry point for *every* strategy.

Strategy authors do **not** write a run script. To add a strategy you only:

1. add ``feature_strategies/strategies/<name>.py`` (config + ``build_specs`` + strategy class);
2. register it in ``feature_strategies/registry.py``;
3. add ``feature_strategies/configs/<name>.yaml`` to choose parameters.

Then run it through this shared script::

    python -m feature_strategies.run_strategy --config feature_strategies/configs/<name>.yaml
    python -m feature_strategies.run_strategy --strategy <name>

This file contains no strategy-specific signal logic; it only wires the
registry, config, synthetic data, and the shared FeatureStrategyRunner together.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from feature_strategies.registry import get_entry
from nautilus_ext.features.examples.synthetic_bars import ONE_SECOND_NS, make_bars
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


def _synthetic_bars(data: dict[str, Any]):
    """A generic flat -> rise -> fall price path that exercises crossovers."""
    mode = data.get("mode", "synthetic")
    if mode != "synthetic":
        raise ValueError(f"unsupported data mode {mode!r} (only 'synthetic' is available)")
    instrument = data.get("instrument_id", "BTC/USDT")
    warmup_n = int(data.get("warmup_bars", 20))
    live_n = int(data.get("live_bars", 20))
    warmup_closes = [100.0] * warmup_n
    live_closes = ([100.0] + [110.0] * 3 + [100.0] * 3 + [90.0] * 3 + [80.0] * live_n)[:live_n]
    warmup_bars = make_bars(warmup_closes, instrument_id=instrument)
    live_bars = make_bars(live_closes, instrument_id=instrument, start_ns=len(warmup_bars) * ONE_SECOND_NS)
    return warmup_bars, live_bars


def _fmt(value: float | None) -> str:
    return f"{value:>10.4f}" if value is not None else f"{'—':>10}"


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
    runner = FeatureStrategyRunner(specs, entry.strategy_cls(config_obj))

    warmup_bars, live_bars = _synthetic_bars(cfg.get("data", {}))
    runner.warmup(iter(warmup_bars))

    spec_names = [s.name for s in specs]
    ready = ", ".join(f"{n}={runner.is_ready(n)}" for n in spec_names)
    print(f"[{name}] warmed up on {len(warmup_bars)} bars; ready: {{{ready}}}\n")

    if not cfg.get("output", {}).get("print_table", True):
        for _ in runner.run(live_bars):  # compute signals without printing
            pass
        return

    header = f"{'t(s)':>6}  {'close':>8}  " + "  ".join(f"{n:>10}" for n in spec_names) + "  signal"
    print(header)
    print("-" * len(header))
    for event, snap, signal in runner.run(live_bars):
        vals = "  ".join(_fmt(snap.value(n)) for n in spec_names)
        print(f"{event.event_time_ns // ONE_SECOND_NS:>6}  {event.close:>8.2f}  {vals}  {signal}")


if __name__ == "__main__":
    main()
