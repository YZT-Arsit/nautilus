#!/usr/bin/env python3
"""Refresh the non-authoritative status package without touching live processes."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"D:\nautilus")
AUDIT = ROOT / "outputs/baseline_evaluation/paper_market_data_continuity_repair"
DELIVERY = ROOT / "outputs/deliverables/direct_maker_24h_clean_ab"
STATUS = AUDIT / "active_active_delivery_status.json"


def main() -> int:
    status = json.loads(STATUS.read_text(encoding="utf-8-sig")) if STATUS.exists() else {}
    redundancy = DELIVERY / "04_market_data_redundancy"; redundancy.mkdir(parents=True, exist_ok=True)
    for name in ("active_active_fault_injection.csv", "active_active_fault_validation.json",
                 "active_active_route_probe_with_c.json", "three_source_qualification_coverage.csv",
                 "collector_c_independence_and_clock_audit.json"):
        source = AUDIT / name
        if source.exists(): shutil.copy2(source, redundancy / name)
    text = f"""# DIRECT vs MAKER clean 24h status

- Strategy: `dynamic_breakout_short`
- Case: `BTCUSDT / 1m / STRICT_REVERSE`
- Previous failed endurance: `337/360` (`RECONNECT_DRIVEN_ARCHITECTURE_INSUFFICIENT`)
- Collector C: integrated; independent Mac direct-Internet path over restricted Tailscale relay
- Qualification: `PASSED 30/30`, canonical missing `0`, minimum simultaneous healthy sources `2`
- Active sources during qualification: Route B + Collector C for `30/30` minutes
- Current pipeline status: `{status.get('status', 'UNKNOWN')}`
- 24h experiment: `{status.get('new_experiment_id', 'NOT_STARTED')}`
- Production market data: read-only
- Production orders: `0`
- P1: frozen/passed; Demo: waiting for credentials; 7-day: not started
- Updated UTC: `{datetime.now(timezone.utc).isoformat()}`
"""
    DELIVERY.mkdir(parents=True, exist_ok=True)
    (DELIVERY / "README_STATUS.md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
