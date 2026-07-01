#!/usr/bin/env python3
"""Top-level user entry point - run any registered strategy.

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
from feature_engine.runner import FeatureStrategyRunner

# Repository root - relative config paths (e.g. a plugin's default_config_path)
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
        cfg = yaml.safe_load(_resolve(config_path).read_text(encoding="utf-8")) or {}
    if args.strategy:
        cfg["strategy"] = args.strategy  # --strategy overrides cfg["strategy"]
    return cfg


def _build_config_obj(config_cls: type, params: dict[str, Any]):
    """Instantiate a config dataclass, ignoring params it doesn't declare."""
    allowed = {f.name for f in fields(config_cls)}
    return config_cls(**{k: v for k, v in params.items() if k in allowed})


def _fee_label(fee: float) -> str:
    """Stable directory label for a fee rate: 0 -> 'nofee', 0.0005 -> 'fee_5bps'."""
    f = float(fee)
    if f == 0:
        return "nofee"
    bps = f * 10_000
    return f"fee_{int(bps)}bps" if bps == int(bps) else f"fee_{bps:g}bps".replace(".", "p")


def run_config(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one resolved config end-to-end. Returns one summary per fee scenario.

    Shared by the CLI (``main``) and the batch runner (``run_batch.py``) so both
    take the exact same data -> features -> signals -> backend path. Each returned
    dict has ``run_name`` / ``fee`` / ``output_dir`` for downstream aggregation.
    """
    name = cfg.get("strategy")
    if not name:
        raise ValueError("config has no 'strategy' key")

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

    # Fee scenarios (first-class with/without-fee comparison). ``fee_scenarios``
    # lists the fee rates to backtest; each produces its own report directory so a
    # no-fee vs with-fee comparison is a single command. ``fee_rate`` (scalar) is
    # the single-scenario shorthand. With no execution backend, neither applies.
    exec_cfg = dict(cfg.get("execution", {}))
    fee_scenarios = exec_cfg.get("fee_scenarios")
    if fee_scenarios is None:
        fee_scenarios = [exec_cfg.get("fee_rate", 0.0)] if exec_cfg.get("backend") else []
    multi = len(fee_scenarios) > 1
    base_run = cfg.get("run_name") or name

    # The signal loop is deterministic, so run it ONCE and replay the recorded
    # stream into each fee scenario's backend (no recompute, identical signals).
    if print_table:
        output.print_event_table_header(spec_names)
    records: list[tuple[Any, Any, str]] = []
    for event, snapshot, signal in runner.run(live_events):
        if print_table:
            output.print_event_row(event, snapshot, signal, spec_names)
        if recorder is not None:
            recorder.record(event, snapshot, signal)
        records.append((event, snapshot, signal))
    if recorder is not None:
        output.print_signal_summary(recorder.signal_counts())

    results: list[dict[str, Any]] = []
    for fee in fee_scenarios:
        per_exec = {k: v for k, v in exec_cfg.items() if k != "fee_scenarios"}
        per_exec["fee_rate"] = float(fee)
        run_name = f"{base_run}/{_fee_label(fee)}" if multi else base_run
        per_ctx = {
            "run_name": run_name,
            "output": output_cfg,
            "data": data_cfg,
            "config": cfg,
            "repo_root": str(_REPO_ROOT),
        }
        backend = build_backend(per_exec, spec_names, per_ctx)
        output_dir = None
        if output_cfg.get("root"):
            output_dir = str(_resolve(output_cfg["root"]) / run_name)
        if backend is not None:
            for event, snapshot, signal in records:
                backend.on_signal(event, snapshot, signal)
            backend.close()
        results.append({"run_name": run_name, "fee": float(fee), "output_dir": output_dir})
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a registered feature strategy")
    parser.add_argument("--config", type=str, help="path to a strategy YAML config")
    parser.add_argument("--strategy", type=str, help="registered strategy name (overrides config)")
    args = parser.parse_args(argv)

    if not args.config and not args.strategy:
        parser.error("provide --strategy NAME and/or --config PATH")

    cfg = _load_config(args)
    if not cfg.get("strategy"):
        parser.error("no strategy given: pass --strategy NAME or a --config with a 'strategy:' key")
    run_config(cfg)


if __name__ == "__main__":
    main()
