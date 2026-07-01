#!/usr/bin/env python3
"""Batch backtest runner — multi-symbol × param-grid × fee-scenario.

Reuses ``run_strategy.run_config`` so every run takes the exact same
data → features → signals → backend path as a single run. After all runs it
scans each report's ``metrics.json`` and writes one evaluation table.

Usage::

    python run_batch.py --config configs/backtests/vwm_batch_smoke_btcusdt.yaml

Batch config schema::

    batch_name: vwm_multi
    strategy: vwm_short
    symbols: [BTCUSDT, ETHUSDT]           # iterated; {symbol} is substituted below
    params: {mom_len: 5, avg_len: 20}     # base strategy params
    param_grid: {atr_pcnt: [0.25, 0.5]}   # optional cartesian grid over params
    data:                                 # template; "{symbol}" substituted per run
      mode: parquet_bars
      root: historical_data/market_data
      symbol: "{symbol}"
      instrument_id: "{symbol}.BINANCE"
      start: 2026-03-01
      end: 2026-05-31
    execution:
      backend: nautilus_backtest
      mode: nautilus_native
      fee_scenarios: [0.0, 0.0005]        # no-fee vs with-fee, per symbol/param
    output:
      root: outputs/batches
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
from pathlib import Path
from typing import Any

import yaml

from run_strategy import run_config

_REPO_ROOT = Path(__file__).resolve().parent

# metrics.json fields surfaced in the evaluation table (best-effort).
_METRIC_COLS = (
    "total_return",
    "max_drawdown",
    "net_pnl",
    "net_realized_pnl",
    "total_commission",
    "trade_count",
    "win_rate",
    "final_equity",
)


def _subst(obj: Any, symbol: str) -> Any:
    """Recursively substitute ``{symbol}`` in strings of a config template."""
    if isinstance(obj, str):
        return obj.replace("{symbol}", symbol)
    if isinstance(obj, dict):
        return {k: _subst(v, symbol) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_subst(v, symbol) for v in obj]
    return obj


def _param_combos(grid: dict[str, list]) -> list[dict[str, Any]]:
    """Cartesian product of a ``{param: [values]}`` grid → list of param dicts."""
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def _param_tag(combo: dict[str, Any]) -> str:
    return "_".join(f"{k}{v}" for k, v in combo.items())


def _build_run_cfg(batch: dict, symbol: str, combo: dict[str, Any]) -> dict:
    """Assemble a single-run config for one (symbol, param-combo)."""
    params = {**batch.get("params", {}), **combo}
    tag = _param_tag(combo)
    run_name = f"{batch['batch_name']}/{symbol}" + (f"__{tag}" if tag else "")
    return {
        "strategy": batch["strategy"],
        "run_name": run_name,
        "params": params,
        "data": _subst(dict(batch.get("data", {})), symbol),
        "execution": dict(batch.get("execution", {})),
        "output": dict(batch.get("output", {"root": "outputs/batches"})),
    }


def _read_metrics(output_dir: str | None) -> dict[str, Any]:
    if not output_dir:
        return {}
    path = Path(output_dir) / "metrics.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a batch of backtests")
    parser.add_argument("--config", required=True, help="path to a batch YAML config")
    args = parser.parse_args(argv)

    batch = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    batch.setdefault("batch_name", Path(args.config).stem)
    symbols = batch.get("symbols") or [batch.get("symbol")]
    if not symbols or symbols == [None]:
        parser.error("batch config needs a 'symbols' list (or a 'symbol')")
    combos = _param_combos(batch.get("param_grid", {}))

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        for combo in combos:
            cfg = _build_run_cfg(batch, symbol, combo)
            print(f"[batch] {cfg['run_name']} ...")
            try:
                results = run_config(cfg)
            except Exception as exc:  # noqa: BLE001 — record failure, keep going
                print(f"[batch]   FAILED: {exc}")
                rows.append({"symbol": symbol, "params": _param_tag(combo),
                             "fee": None, "status": "failed", "error": str(exc)})
                continue
            for res in results:
                metrics = _read_metrics(res.get("output_dir"))
                row = {
                    "symbol": symbol,
                    "params": _param_tag(combo),
                    "fee": res.get("fee"),
                    "status": "ok",
                    "run_name": res.get("run_name"),
                    "output_dir": res.get("output_dir"),
                }
                row.update({c: metrics.get(c) for c in _METRIC_COLS})
                rows.append(row)

    _write_table(batch, rows)


def _write_table(batch: dict, rows: list[dict[str, Any]]) -> None:
    root = batch.get("output", {}).get("root", "outputs/batches")
    base = Path(root)
    if not base.is_absolute():
        base = _REPO_ROOT / base
    out_dir = base / batch["batch_name"]
    out_dir.mkdir(parents=True, exist_ok=True)

    cols = ["symbol", "params", "fee", "status", *_METRIC_COLS, "run_name", "output_dir", "error"]
    csv_path = out_dir / "evaluation_table.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    md = ["# Batch evaluation: " + batch["batch_name"], ""]
    head = ["symbol", "params", "fee", "total_return", "max_drawdown", "trade_count", "win_rate", "status"]
    md.append("| " + " | ".join(head) + " |")
    md.append("|" + "|".join(["---"] * len(head)) + "|")
    for r in rows:
        md.append("| " + " | ".join(str(r.get(h, "")) for h in head) + " |")
    (out_dir / "evaluation_table.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"[batch] wrote {csv_path}")
    print(f"[batch] wrote {out_dir / 'evaluation_table.md'}")


if __name__ == "__main__":
    main()
