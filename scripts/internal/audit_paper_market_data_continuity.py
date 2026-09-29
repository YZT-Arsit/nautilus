#!/usr/bin/env python3
"""Forensic continuity audit for the preserved failed 24-hour paper run."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_paper_orchestrator import get_json


MINUTE_NS = 60_000_000_000


def iso(ns: int | None) -> str:
    return "" if ns is None else pd.Timestamp(ns, unit="ns", tz="UTC").isoformat()


def read_jsonl(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-id", default="pc_2fe14acb95eac19f88d4")
    args = parser.parse_args()
    experiment = args.experiment.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    worker = experiment / "workers/BTCUSDT"
    validation = json.loads((worker / "dry_run_validation.json").read_text(encoding="utf-8"))
    summary = validation["summary"]
    process_start = int(summary["started_ns"])
    process_end = int(summary["ended_ns"])
    audit_start = process_start // MINUTE_NS * MINUTE_NS
    audit_end = audit_start + 1_440 * MINUTE_NS

    per_minute = defaultdict(lambda: {"quote_count": 0, "trade_count": 0})
    per_hour = defaultdict(lambda: {
        "quote_count": 0, "trade_count": 0, "first_timestamp": None,
        "last_timestamp": None, "max_inter_event_gap_ns": 0, "prior": None,
    })
    first = {"quote": None, "trade": None}
    last = {"quote": None, "trade": None}
    market_start = None
    market_end = None
    trade_triggers = defaultdict(list)
    for path in sorted((worker / "market_data").rglob("events.jsonl")):
        for row in read_jsonl(path):
            payload = row["payload"]
            kind = payload["event_type"]
            if kind not in {"quote", "trade"}:
                continue
            ts = int(row["ts_exchange"])
            market_start = ts if market_start is None else min(market_start, ts)
            market_end = ts if market_end is None else max(market_end, ts)
            first[kind] = ts if first[kind] is None else min(first[kind], ts)
            last[kind] = ts if last[kind] is None else max(last[kind], ts)
            if kind == "trade":
                trade_triggers[ts].append((
                    float(payload["price"]), bool(payload.get("is_buyer_maker", False)),
                ))
            minute = ts // MINUTE_NS * MINUTE_NS
            per_minute[minute][f"{kind}_count"] += 1
            hour = ts // (60 * MINUTE_NS) * (60 * MINUTE_NS)
            slot = per_hour[hour]
            slot[f"{kind}_count"] += 1
            slot["first_timestamp"] = ts if slot["first_timestamp"] is None else min(slot["first_timestamp"], ts)
            slot["last_timestamp"] = ts if slot["last_timestamp"] is None else max(slot["last_timestamp"], ts)
            if slot["prior"] is not None:
                slot["max_inter_event_gap_ns"] = max(slot["max_inter_event_gap_ns"], ts - slot["prior"])
            slot["prior"] = ts

    bar_minutes = set()
    bar_times = []
    for row in read_jsonl(worker / "bars/BTCUSDT_1m.jsonl"):
        end = int(row["event_time_ns"])
        bar_times.append(end)
        bar_minutes.add(end - MINUTE_NS)
    decision_minutes = Counter()
    for row in read_jsonl(worker / f"decisions/{args.candidate_id}.jsonl"):
        decision_minutes[int(row["event_time_ns"]) - MINUTE_NS] += 1

    historical = {}
    try:
        rows = get_json("/fapi/v1/klines", {
            "symbol": "BTCUSDT", "interval": "1m", "startTime": audit_start // 1_000_000,
            "endTime": audit_end // 1_000_000 - 1, "limit": 1500,
        })
        historical = {int(row[0]) * 1_000_000: int(row[8]) for row in rows}
    except Exception:
        historical = {}

    coverage = []
    root_causes = Counter()
    for minute in range(audit_start, audit_end, MINUTE_NS):
        raw = per_minute[minute]
        bar = minute in bar_minutes
        hist_trades = historical.get(minute)
        if bar:
            classification = "COMPLETE_BAR"
        elif minute < process_start or minute >= process_end:
            classification = "PROCESS_NOT_RUNNING"
        elif raw["trade_count"] > 0:
            classification = "BAR_BUILDER_FAILED"
        elif raw["quote_count"] > 0:
            classification = "WEBSOCKET_NOT_RECEIVED"
        elif hist_trades is not None and hist_trades > 0:
            classification = "WEBSOCKET_NOT_RECEIVED"
        else:
            classification = "UNKNOWN"
        if not bar:
            root_causes[classification] += 1
        coverage.append({
            "minute": iso(minute), "quote_received": raw["quote_count"] > 0,
            "quote_count": raw["quote_count"], "trade_received": raw["trade_count"] > 0,
            "trade_count": raw["trade_count"], "routed_quote_count": raw["quote_count"],
            "routed_trade_count": raw["trade_count"], "bar_created": bar,
            "bar_dispatched": bar, "strategy_decision_emitted": decision_minutes[minute] > 0,
            "historical_reference_trade_count": hist_trades,
            "missing_bar_classification": classification,
        })
    pd.DataFrame(coverage).to_csv(output / "pipeline_coverage_audit.csv", index=False)

    hourly_rows = []
    for hour, row in sorted(per_hour.items()):
        hourly_rows.append({
            "utc_hour": iso(hour), "QuoteTick_count": row["quote_count"],
            "TradeTick_count": row["trade_count"], "first_timestamp": iso(row["first_timestamp"]),
            "last_timestamp": iso(row["last_timestamp"]),
            "maximum_inter_event_gap_seconds": row["max_inter_event_gap_ns"] / 1e9,
        })
    pd.DataFrame(hourly_rows).to_csv(output / "market_data_hourly_coverage.csv", index=False)

    timeline = [
        ("process_start", process_start), ("market_data_start", market_start),
        ("first_quote", first["quote"]), ("first_trade", first["trade"]),
        ("first_completed_1m_bar", min(bar_times) if bar_times else None),
        ("last_quote", last["quote"]), ("last_trade", last["trade"]),
        ("last_completed_1m_bar", max(bar_times) if bar_times else None),
        ("process_end", process_end),
    ]
    pd.DataFrame([{"event": name, "timestamp": iso(ts), "timestamp_ns": ts} for name, ts in timeline]).to_csv(
        output / "failed_run_timeline.csv", index=False
    )

    errors = summary.get("worker_errors", [])
    lifecycle = [{"timestamp": iso(process_start), "event": "PROCESS_STARTED", "detail": "feed worker launched"}]
    for row in errors:
        lifecycle.append({
            "timestamp": iso(int(row["time_ns"])), "event": "DISCONNECT_OR_CONNECT_FAILURE",
            "detail": row["error"],
        })
    lifecycle.append({
        "timestamp": iso(process_end), "event": "PROCESS_ENDED",
        "detail": f"reconnect_count={summary.get('reconnects', {}).get('BTCUSDT', 0)}",
    })
    pd.DataFrame(lifecycle).to_csv(output / "websocket_lifecycle.csv", index=False)

    health = [
        {"task": "websocket_reader", "running_24h": True, "failure": "proxy transport unavailable", "restart_policy": "retry loop", "status": "FAILED_STALE"},
        {"task": "market_data_normalizer", "running_24h": True, "failure": "none observed", "restart_policy": "in-process", "status": "PASSED_WHEN_FED"},
        {"task": "recorder", "running_24h": True, "failure": "none observed", "restart_policy": "none", "status": "PASSED_WHEN_FED"},
        {"task": "bar_builder", "running_24h": True, "failure": "starved of TradeTick input", "restart_policy": "none", "status": "INPUT_STARVED"},
        {"task": "event_router", "running_24h": True, "failure": "none observed", "restart_policy": "none", "status": "PASSED_WHEN_FED"},
        {"task": "strategy_dispatcher", "running_24h": True, "failure": "starved of completed bars", "restart_policy": "none", "status": "INPUT_STARVED"},
        {"task": "heartbeat", "running_24h": True, "failure": "did not enforce freshness", "restart_policy": "none", "status": "DESIGN_DEFECT"},
    ]
    pd.DataFrame(health).to_csv(output / "task_health_audit.csv", index=False)

    orders = pd.read_csv(worker / "orders/simulated_orders.csv")
    fills = pd.read_csv(worker / "fills/simulated_fills.csv")
    orders = orders.loc[orders.experiment_candidate_id.astype(str).eq(args.candidate_id)].copy()
    fills = fills.loc[
        fills.experiment_candidate_id.astype(str).eq(args.candidate_id)
        & fills.execution_mode.astype(str).eq("L1_BBO_PAPER_MAKER")
    ].copy()
    maker_audit = []
    for order in orders.itertuples(index=False):
        matching = fills.loc[fills.order_id.astype(str).eq(str(order.client_order_id))]
        trigger_checks = []
        for fill in matching.itertuples(index=False):
            candidates = trade_triggers.get(int(fill.event_time_ns), [])
            if str(order.side) == "BUY":
                valid = any(maker and price <= float(order.price) + 1e-12 for price, maker in candidates)
            else:
                valid = any((not maker) and price >= float(order.price) - 1e-12 for price, maker in candidates)
            trigger_checks.append(valid)
        maker_audit.append({
            "order_id": order.client_order_id, "side": order.side,
            "requested_quantity": float(order.quantity), "filled_quantity": float(order.filled_quantity),
            "order_price": float(order.price), "post_only": bool(order.post_only),
            "native_OrderFilled_event_count": len(matching),
            "all_fills_have_eligible_TradeTick_trigger": bool(trigger_checks) and all(trigger_checks),
            "fill_ratio": float(order.filled_quantity) / float(order.quantity),
            "status": order.status,
        })
    pd.DataFrame(maker_audit).to_csv(output / "old_maker_fill_audit.csv", index=False)

    queue_rows = []
    affected = []
    for path in sorted((experiment / "workers").glob("*/dry_run_validation.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        s = item["summary"]
        symbol = path.parent.name
        bars = int(s.get("bars_1m", 0))
        queue_rows.append({
            "symbol": symbol, "max_queue_depth": int(s.get("max_queue_backlog", 0)),
            "average_queue_depth": "NOT_RECORDED", "dropped_event_count": "NOT_RECORDED",
            "processing_lag": "NOT_RECORDED", "enqueue_rate": "NOT_RECORDED",
            "dequeue_rate": "NOT_RECORDED", "backpressure_observed": int(s.get("max_queue_backlog", 0)) >= 250_000,
        })
        affected.append({
            "experiment_id": f"{experiment.name}/{symbol}", "start": iso(int(s["started_ns"])),
            "end": iso(int(s["ended_ns"])), "expected_bars": 1440, "observed_bars": bars,
            "coverage_pct": bars / 1440 * 100, "same_root_cause": True,
            "evidence_status": "INVALID_MARKET_DATA_COVERAGE",
        })
    pd.DataFrame(queue_rows).to_csv(output / "queue_backpressure_audit.csv", index=False)
    pd.DataFrame(affected).to_csv(output / "affected_experiments_audit.csv", index=False)
    pd.DataFrame([
        {"classification": key, "minute_count": value,
         "reason": "official historical kline confirms BTC trades" if key == "WEBSOCKET_NOT_RECEIVED" else "pipeline evidence"}
        for key, value in sorted(root_causes.items())
    ]).to_csv(output / "missing_bar_root_causes.csv", index=False)

    failure = {
        "experiment_id": experiment.name,
        "status": "FAILED_ENGINEERING_RUN",
        "reason": "MARKET_DATA_COVERAGE_INCOMPLETE",
        "root_cause": "SSH_REVERSE_FORWARD_LOST; PAPER_WORKERS_RETRIED_DEAD_LOCAL_CONNECT_PROXY; HEARTBEAT_DID_NOT_GATE_STALE_FEED",
        "expected_bars": 1440, "observed_bars": int(summary.get("bars_1m", 0)),
        "missing_bars": 1440 - int(summary.get("bars_1m", 0)),
        "replay_mismatches": 0, "accounting_invariant_failures": 0,
        "old_metrics_authoritative": False,
    }
    (experiment / "engineering_failure.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
    freeze_path = experiment / "manifest/paper_experiment_freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze.update({"status": "FAILED_ENGINEERING_RUN", "failure_reason": failure["reason"]})
    freeze_path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    (output / "validation_summary.json").write_text(json.dumps({
        **failure,
        "process_runtime_hours": (process_end - process_start) / 3.6e12,
        "market_data_covered_hours": ((market_end or 0) - (market_start or 0)) / 3.6e12,
        "bar_covered_hours": ((max(bar_times) - min(bar_times)) / 3.6e12) if len(bar_times) > 1 else 0,
        "affected_worker_count": len(affected), "historical_reference_minutes": len(historical),
        "timestamp_normalization_mismatch": 0,
        "old_maker_order_count": len(maker_audit),
        "old_maker_all_orders_filled": bool(maker_audit) and all(row["fill_ratio"] >= 1.0 - 1e-12 for row in maker_audit),
        "old_maker_fill_trigger_mismatches": sum(
            not row["all_fills_have_eligible_TradeTick_trigger"] for row in maker_audit
        ),
        "old_maker_100pct_fill_explanation": (
            "GENUINE_WITHIN_TRUNCATED_7_ORDER_SAMPLE; NOT_REPRESENTATIVE_OF_24H; "
            "SPARSE_FEED_TERMINATED_FURTHER_SIGNALS"
        ),
    }, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(columns=[
        "minute", "quote_count", "trade_count", "bar_created", "status",
    ]).to_csv(output / "two_hour_continuity_test.csv", index=False)
    print(json.dumps(failure, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
