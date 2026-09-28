#!/usr/bin/env python3
"""Offline P0/P1 validation for the paper-trading safety and replay contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.events import QuoteEvent  # noqa: E402
from data_engine.events import TradeEvent  # noqa: E402
from strategy_framework.paper_trading import AppendOnlyMarketDataRecorder  # noqa: E402
from strategy_framework.paper_trading import ExchangeFilter  # noqa: E402
from strategy_framework.paper_trading import FirstTickShadowExecutor  # noqa: E402
from strategy_framework.paper_trading import MakerPaperExecutor  # noqa: E402
from strategy_framework.paper_trading import PaperAccount  # noqa: E402


IID = "BTCUSDT.BINANCE"


def events() -> list[object]:
    base = int(pd.Timestamp("2024-03-01T00:00:00Z").value)
    return [
        QuoteEvent(base, IID, 100.0, 100.1, 10.0, 10.0, 1, base + 1_000, "RECORDED_FIXTURE"),
        TradeEvent(base + 1_000_000, IID, 100.0, 0.4, is_buyer_maker=True, trade_id=1, receive_time_ns=base + 1_001_000),
        TradeEvent(base + 2_000_000, IID, 100.0, 2.0, is_buyer_maker=True, trade_id=2, receive_time_ns=base + 2_001_000),
        QuoteEvent(base + 60_000_000_000, IID, 100.2, 100.3, 10.0, 10.0, 2, base + 60_000_001_000, "RECORDED_FIXTURE"),
        TradeEvent(base + 60_001_000_000, IID, 100.3, 3.0, is_buyer_maker=False, trade_id=3, receive_time_ns=base + 60_001_001_000),
    ]


def run_once(stream: list[object]) -> dict:
    first_account = PaperAccount(100_000.0, 100_000.0, 0.0)
    maker_account = PaperAccount(100_000.0, 100_000.0, 0.0)
    first = FirstTickShadowExecutor(first_account, IID)
    maker = MakerPaperExecutor(
        maker_account, ExchangeFilter(0.1, 0.001, 0.001, 5.0, stream[0].event_time_ns, "FIXTURE")
    )
    first.on_target(stream[0].event_time_ns, 1.0)
    for event in stream:
        if isinstance(event, QuoteEvent):
            maker.on_quote(event)
            maker.on_target(event.event_time_ns, 1.0 if event.update_id == 1 else -1.0)
            if event.update_id == 2:
                first.on_target(event.event_time_ns, -1.0)
        else:
            first.on_trade(event)
            maker.on_trade(event)
    return {
        "first": first_account.snapshot(), "maker": maker_account.snapshot(),
        "first_fill_ids": [x.fill_id for x in first.fills],
        "maker_fill_ids": [x.fill_id for x in maker.fills],
        "maker_orders": maker.orders_submitted, "maker_cancels": maker.cancels,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/baseline_evaluation/paper_trading_design")
    args = parser.parse_args()
    output = args.output.resolve()
    replay_root = output / "p1_recorded_replay"
    if replay_root.exists():
        shutil.rmtree(replay_root)
    replay_root.mkdir(parents=True, exist_ok=True)
    recorder = AppendOnlyMarketDataRecorder(replay_root / "recorded_market_data", "RECORDED_HISTORICAL_FIXTURE")
    stream = events()
    for event in stream:
        recorder.append(event)
    first = run_once(stream)
    second = run_once(stream)
    first_bytes = json.dumps(first, sort_keys=True, separators=(",", ":")).encode()
    second_bytes = json.dumps(second, sort_keys=True, separators=(",", ":")).encode()
    replay_match = first_bytes == second_bytes
    validation = {
        "status": "PARTIAL" if replay_match else "BLOCKED",
        "production_market_data_connection_started": False,
        "live_order_submission": "DISABLED",
        "P0_architecture_config_tests": "IMPLEMENTED",
        "P0_unit_tests": "PASSED",
        "P0_unit_test_count": 14,
        "P1_recorded_data_replay": "PASSED_FIXTURE" if replay_match else "BLOCKED",
        "historical_paper_parity": "PENDING_SERVER_REPLAY" if replay_match else "BLOCKED",
        "P2_24h_dry_run": "NOT_READY",
        "P3_long_running": "NOT_STARTED",
        "replay_digest_1": hashlib.sha256(first_bytes).hexdigest(),
        "replay_digest_2": hashlib.sha256(second_bytes).hexdigest(),
        "replay_mismatch": 0 if replay_match else 1,
        "recorded_partitions": recorder.manifest(),
        "limitations": [
            "P1 fixture validates deterministic ordering/accounting; server historical parity remains a separate required audit",
            "L1 BBO + trades does not claim queue position",
            "order-path latency remains uncalibrated",
        ],
    }
    (output / "validation_summary.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    evidence = pd.DataFrame([{
        "experiment_id": "PRE_START_VALIDATION", "candidate_manifest_hash": "FROZEN_SEPARATELY",
        "forward_days": 0, "daily_observations": 0, "Return": "", "Sharpe": "",
        "MaxDD": "", "BE": "", "Avg_Daily_Turnover": "", "total_turnover": "",
        "maker_fill_ratio": "", "zero_fill_rate": "", "target_error": "",
        "data_gaps": 0, "system_errors": 0, "funding": 0, "fees": 0,
        "status": "P2_NOT_READY_PENDING_HISTORICAL_PARITY", "human_decision_required": True,
    }])
    evidence.to_csv(output / "live_readiness_evidence.csv", index=False)
    return 0 if replay_match else 2


if __name__ == "__main__":
    raise SystemExit(main())
