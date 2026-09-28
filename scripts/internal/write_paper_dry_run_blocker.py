#!/usr/bin/env python3
"""Write an explicit P2 blocker report without opening a market-data connection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    root = args.experiment.resolve()
    manifest = pd.read_csv(root / "manifest/paper_candidate_manifest.csv")
    summary = pd.DataFrame([{
        "status": "BLOCKED_PRE_START",
        "experiment_id": root.name,
        "actual_hours": 0.0,
        "candidate_cases": len(manifest),
        "strategy_ids": manifest.strategy_id.nunique(),
        "symbols": manifest.symbol.nunique(),
        "market_data_events": 0,
        "first_tick_shadow_fills": 0,
        "maker_simulated_orders": 0,
        "maker_full_fills": 0,
        "maker_partial_fills": 0,
        "maker_zero_fills": 0,
        "live_exchange_orders": 0,
        "Sharpe": "INSUFFICIENT_DAILY_OBSERVATIONS",
        "blocker": args.reason,
    }])
    summary.to_csv(root / "dry_run_summary.csv", index=False)
    validation = {
        "status": "BLOCKED",
        "experiment_id": root.name,
        "production_market_data_connection_started": False,
        "live_exchange_orders": 0,
        "candidate_manifest_frozen": True,
        "P2_24h_dry_run": "NOT_STARTED",
        "P3_7d_forward": "NOT_STARTED",
        "blocker": args.reason,
    }
    (root / "dry_run_validation.json").write_text(
        json.dumps(validation, indent=2) + "\n", encoding="utf-8",
    )
    report = f"""# 24-hour paper dry-run system report

Status: **BLOCKED PRE-START**

Experiment: `{root.name}`

Frozen candidate cases: {len(manifest)}

Live exchange orders: 0

Production market-data connection started: NO

## Blocker

{args.reason}

Starting the public market-data recorder alone would not exercise the frozen
strategies, decisions, simulated orders, funding, or accounting end to end and
therefore is not reported as a paper-trading dry run.
"""
    (root / "dry_run_system_report.md").write_text(report, encoding="utf-8")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
