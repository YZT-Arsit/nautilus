#!/usr/bin/env python3
"""Freeze the Binance Demo A/B package without submitting any order.

This is deliberately a packaging and fail-closed credential gate.  It never
constructs an authenticated client and cannot place an exchange order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import pandas as pd
import yaml


EXPECTED = {
    "strategy_id": "dynamic_breakout_short",
    "symbol": "BTCUSDT",
    "timeframe": "1m",
    "direction_variant": "STRICT_REVERSE",
}
MODES = ("03_DEMO_DIRECT_REVERSE", "04_DEMO_MAKER_REVERSE")
CSV_SCHEMAS = {
    "account_snapshot.csv": ["timestamp", "account_fingerprint", "wallet_balance", "position_qty", "status"],
    "orders.csv": ["timestamp", "decision_id", "client_order_id", "side", "order_type", "price", "quantity", "status"],
    "fills.csv": ["timestamp", "decision_id", "client_order_id", "trade_id", "side", "price", "quantity", "commission", "commission_asset"],
    "funding.csv": ["timestamp", "income_type", "amount", "asset", "status"],
    "fees.csv": ["timestamp", "trade_id", "fee", "asset", "effective_fee_bps", "status"],
    "daily_turnover.csv": ["utc_date", "daily_turnover_raw", "daily_turnover_pct", "status"],
    "performance.csv": ["timestamp", "equity", "return", "turnover_raw", "drawdown", "status"],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def empty_csv(path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=columns).to_csv(path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--server-root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if config["environment"] != "BINANCE_DEMO":
        raise RuntimeError("environment must be BINANCE_DEMO")
    if config["demo_http_endpoint"] == "https://fapi.binance.com":
        raise RuntimeError("production trading endpoint is forbidden")
    source = pd.read_csv(args.source_manifest)
    mask = pd.Series(True, index=source.index)
    for key, value in EXPECTED.items():
        mask &= source[key].astype(str).eq(value)
    selected = source.loc[mask].copy()
    if len(selected) != 1:
        raise RuntimeError(f"expected exactly one authoritative candidate row, found {len(selected)}")
    selected["historical_reference_mode"] = "03_FIRST_TICK_REVERSE / 04_MAKER_REVERSE"
    selected["target_definition"] = "target_reverse(t)=-target_normal(t)"
    selected["source_manifest"] = str(args.source_manifest)
    selected["source_manifest_sha256"] = sha256(args.source_manifest)
    selected["deployment_config_sha256"] = sha256(args.config)

    env_names = list(config["credentials"].values())
    credential_presence = {name: bool(os.environ.get(name)) for name in env_names}
    credentials_complete = all(credential_presence.values())
    # Account isolation cannot be asserted from key presence.  It requires
    # authenticated account snapshots and the two controlled Demo order cycles.
    isolated = False
    gate_status = (
        "TWO_DEMO_ACCOUNTS_REQUIRED_FOR_CLEAN_AB_TEST"
        if not credentials_complete else "ACCOUNT_ISOLATION_VERIFICATION_REQUIRED"
    )
    root = args.server_root.resolve()
    case = root / "dynamic_breakout_short" / "BTCUSDT_1m_STRICT_REVERSE"
    comparison = case / "comparison"
    for mode in MODES:
        mode_root = case / mode
        (mode_root / "figures").mkdir(parents=True, exist_ok=True)
        for name, columns in CSV_SCHEMAS.items():
            empty_csv(mode_root / name, columns)
        if mode == "04_DEMO_MAKER_REVERSE":
            empty_csv(mode_root / "maker_execution_metrics.csv", [
                "orders", "full_fills", "partial_fills", "zero_fills",
                "quantity_fill_ratio", "post_only_reject_count", "cancel_count", "status",
            ])
        pd.DataFrame([{"status": gate_status, "production_orders": 0}]).to_csv(
            mode_root / "DEPLOYMENT_BLOCKED.csv", index=False
        )
    comparison.mkdir(parents=True, exist_ok=True)
    empty_csv(comparison / "execution_comparison.csv", [
        "mode", "Return", "MaxDD", "Avg_Daily_Turnover_pct", "Total_Turnover_raw",
        "fees", "funding", "fill_latency_ms", "status",
    ])
    empty_csv(comparison / "decision_alignment.csv", [
        "decision_id", "bar_close_timestamp", "NORMAL_target", "STRICT_REVERSE_target",
        "DIRECT_target", "MAKER_target", "direct_mismatch", "maker_mismatch",
    ])
    pd.DataFrame([{
        "environment": "BINANCE_DEMO",
        "market_data_endpoint": config["demo_market_ws_endpoint"],
        "trading_endpoint": config["demo_http_endpoint"],
        "production_endpoint_used": False,
        "production_order_count": 0,
        "status": gate_status,
    }]).to_csv(comparison / "data_quality.csv", index=False)
    empty_csv(case / "signal_decisions.csv", [
        "decision_id", "bar_close_timestamp", "NORMAL_target", "STRICT_REVERSE_target",
    ])
    manifest_dir = root / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    selected.to_csv(manifest_dir / "frozen_strategy_manifest.csv", index=False)
    shutil.copy2(args.config, manifest_dir / "demo_ab_config.yaml")
    gate = {
        "status": gate_status,
        "credentials_present": credential_presence,
        "credentials_complete": credentials_complete,
        "demo_accounts_isolated": isolated,
        "demo_http_endpoint": config["demo_http_endpoint"],
        "demo_market_ws_endpoint": config["demo_market_ws_endpoint"],
        "production_trading_client_initialized": False,
        "production_order_count": 0,
        "demo_order_count": 0,
        "direct_order_cycle": "NOT_STARTED",
        "maker_order_cycle": "NOT_STARTED",
        "smoke_30m": "NOT_STARTED",
        "deployment_24h": "NOT_STARTED",
    }
    (manifest_dir / "deployment_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    pd.DataFrame([{
        "check": key, "value": json.dumps(value) if isinstance(value, (dict, list)) else value
    } for key, value in gate.items()]).to_csv(root / "deployment_status.csv", index=False)

    delivery = args.delivery_root.resolve()
    if delivery.exists():
        shutil.rmtree(delivery)
    shutil.copytree(root, delivery)
    print(json.dumps({
        "status": gate_status,
        "server_root": str(root),
        "delivery_root": str(delivery),
        "strategy_code_hash": selected.iloc[0]["strategy_code_hash"],
        "parameter_hash": selected.iloc[0]["parameter_hash"],
        "config_hash": selected.iloc[0]["config_hash"],
        "production_order_count": 0,
    }, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
