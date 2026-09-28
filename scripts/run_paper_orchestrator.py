#!/usr/bin/env python3
"""Run bounded, production-read-only forward paper phases.

There is intentionally no authenticated Binance client and no order endpoint.
All orders are local Nautilus matching-engine objects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict
from dataclasses import replace
from pathlib import Path

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.events import BarEvent  # noqa: E402
from data_engine.live.binance_ws_client import BinancePublicWebSocketSource  # noqa: E402
from data_engine.transforms import resample_bars  # noqa: E402
from strategy_framework.paper_trading.orchestrator import PaperOrchestrator, manifest_hash  # noqa: E402


PUBLIC_REST = "https://fapi.binance.com"
PUBLIC_WS = "wss://fstream.binance.com"


def get_json(path: str, params: dict | None = None):
    suffix = "?" + urllib.parse.urlencode(params) if params else ""
    request = urllib.request.Request(PUBLIC_REST + path + suffix, headers={"User-Agent": "nautilus-paper-research/1"})
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 public read-only endpoint
                return json.loads(response.read())
        except Exception as exc:  # public read-only transport retry
            last_error = exc
            if attempt < 4:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"public REST request failed after retries: {path}") from last_error


def recent_warmup(symbol: str, now_ms: int) -> dict[str, list[BarEvent]]:
    rows = get_json("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 1500})
    raw = [
        BarEvent(
            instrument_id=f"{symbol}-PERP.BINANCE", event_time_ns=int(row[0]) * 1_000_000,
            open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]),
            volume=float(row[5]), quote_volume=float(row[7]), trade_count=int(row[8]),
            taker_buy_volume=float(row[9]), taker_buy_quote_volume=float(row[10]),
        )
        for row in rows if int(row[6]) < now_ms
    ]
    if len(raw) < 1350:
        raise RuntimeError(f"{symbol}: insufficient continuous REST warmup bars ({len(raw)})")
    if any(b.event_time_ns - a.event_time_ns != 60_000_000_000 for a, b in zip(raw, raw[1:])):
        raise RuntimeError(f"{symbol}: warmup bars are not a continuous 1m clock")
    result = {"1m": [replace(bar, event_time_ns=bar.event_time_ns + 60_000_000_000) for bar in raw]}
    for tf, minutes in (("10m", 10), ("15m", 15)):
        result[tf] = [
            replace(bar, event_time_ns=bar.event_time_ns + minutes * 60_000_000_000)
            for bar in resample_bars(raw, tf)
        ]
    return result


def deterministic_subset(manifest: pd.DataFrame, count: int) -> pd.DataFrame:
    ordered = manifest.sort_values(["symbol", "timeframe", "strategy_id", "direction_variant"])
    picks = []
    for _, group in ordered.groupby(["symbol", "timeframe"], sort=True):
        picks.append(group.iloc[[0]])
        if sum(len(x) for x in picks) >= count:
            break
    if sum(len(x) for x in picks) < count:
        used = pd.concat(picks).experiment_candidate_id if picks else pd.Series(dtype=str)
        picks.append(ordered.loc[~ordered.experiment_candidate_id.isin(used)].head(count - sum(len(x) for x in picks)))
    return pd.concat(picks, ignore_index=True).head(count)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--phase", choices=["subset_smoke", "full_manifest_smoke", "authoritative_24h"], required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--subset-count", type=int, default=18)
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--worker-id")
    parser.add_argument("--preflight-cache", type=Path)
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        parser.error("duration must be positive")
    repo, experiment = args.repo.resolve(), args.experiment.resolve()
    manifest_path = experiment / "manifest/paper_candidate_manifest_9symbols.csv"
    expected = (experiment / "manifest/paper_candidate_manifest_9symbols.sha256").read_text().strip()
    if manifest_hash(manifest_path) != expected:
        raise RuntimeError("candidate manifest hash mismatch")
    manifest = pd.read_csv(manifest_path)
    if args.symbols:
        manifest = manifest[manifest.symbol.isin([s.upper() for s in args.symbols])].copy()
        if manifest.empty:
            raise RuntimeError("requested worker symbol set has no frozen candidates")
    if args.phase == "subset_smoke":
        active = deterministic_subset(manifest, args.subset_count)
        run_root = experiment / "preflight/subset_smoke"
    elif args.phase == "full_manifest_smoke":
        active, run_root = manifest, experiment / "preflight/full_manifest_smoke"
    else:
        active, run_root = manifest, experiment
    if args.worker_id:
        run_root = run_root / "workers" / args.worker_id
    run_root.mkdir(parents=True, exist_ok=True)

    config = yaml.safe_load((experiment / "manifest/paper_trading_v1.resolved.yaml").read_text())
    if config.get("order_submission") != "DISABLED" or config.get("credentials_required") is not False:
        raise RuntimeError("hard safety preflight failed")
    symbols = sorted(active.symbol.unique())
    cache = args.preflight_cache.resolve() if args.preflight_cache else None
    exchange_info = (
        json.loads((cache / "exchange_info.json").read_text(encoding="utf-8"))
        if cache else get_json("/fapi/v1/exchangeInfo")
    )
    metadata_bytes = json.dumps(exchange_info, sort_keys=True, separators=(",", ":")).encode()
    metadata_dir = experiment / "manifest/instrument_metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "binance_usdm_exchange_info.json").write_bytes(metadata_bytes)
    (metadata_dir / "binance_usdm_exchange_info.sha256").write_text(hashlib.sha256(metadata_bytes).hexdigest() + "\n")
    now_ms = time.time_ns() // 1_000_000
    warmup: dict[tuple[str, str], list[BarEvent]] = {}
    for symbol in symbols:
        if cache:
            per_tf = {}
            for timeframe in active.loc[active.symbol.eq(symbol), "timeframe"].unique():
                source = cache / "warmup" / f"{symbol}_{timeframe}.jsonl"
                per_tf[timeframe] = [
                    BarEvent(**json.loads(line)) for line in source.read_text(encoding="utf-8").splitlines() if line
                ]
        else:
            per_tf = recent_warmup(symbol, now_ms)
        for timeframe in active.loc[active.symbol.eq(symbol), "timeframe"].unique():
            warmup[(symbol, timeframe)] = per_tf[timeframe]
            warmup_path = run_root / "warmup" / f"{symbol}_{timeframe}.jsonl"
            warmup_path.parent.mkdir(parents=True, exist_ok=True)
            warmup_path.write_text(
                "".join(json.dumps(asdict(bar), sort_keys=True, separators=(",", ":")) + "\n" for bar in per_tf[timeframe]),
                encoding="utf-8",
            )

    orchestrator = PaperOrchestrator(
        repo=repo, experiment=run_root, manifest=active, exchange_info=exchange_info,
        warmup_by_symbol_timeframe=warmup,
        initial_capital=float(config["account"]["initial_capital"]),
        target_notional=float(config["account"]["target_notional"]),
        fee_rate=float(config["fees"]["maker_rate"]),
    )
    event_queue: queue.Queue = queue.Queue(maxsize=250_000)
    stop = threading.Event()
    worker_errors: list[dict] = []
    reconnects = {symbol: 0 for symbol in symbols}
    deadline = time.monotonic() + args.duration_seconds

    def worker(symbol: str) -> None:
        iid = f"{symbol}-PERP.BINANCE"
        while not stop.is_set() and time.monotonic() < deadline:
            remaining = max(1.0, min(60.0, deadline - time.monotonic()))
            source = BinancePublicWebSocketSource(
                symbol, ("trade", "bookTicker", "markPrice@1s"),
                base_url=PUBLIC_WS, instrument_id=iid,
            )
            try:
                for event in source.iter_events(max_messages=1_000_000_000, timeout_seconds=remaining):
                    if stop.is_set(): break
                    event_queue.put(event, timeout=5)
            except Exception as exc:  # reconnect public data only
                reconnects[symbol] += 1
                if len(worker_errors) < 1_000:
                    worker_errors.append({"symbol": symbol, "error": repr(exc), "time_ns": time.time_ns()})
                time.sleep(min(5.0, max(0.1, deadline - time.monotonic())))

    threads = [threading.Thread(target=worker, args=(symbol,), daemon=True, name=f"feed-{symbol}") for symbol in symbols]
    started_ns = time.time_ns()
    if args.phase == "authoritative_24h" and not args.worker_id:
        freeze_path = experiment / "manifest/paper_experiment_freeze.json"
        freeze = json.loads(freeze_path.read_text())
        if freeze.get("forward_start_timestamp") is not None:
            raise RuntimeError("authoritative forward start already frozen")
        freeze["forward_start_timestamp"] = pd.Timestamp(started_ns, unit="ns", tz="UTC").isoformat()
        freeze["status"] = "RUNNING_24H"
        temp = freeze_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(freeze, indent=2) + "\n")
        os.replace(temp, freeze_path)
    for thread in threads: thread.start()
    max_backlog = 0
    last_heartbeat = 0.0
    try:
        while time.monotonic() < deadline:
            try:
                event = event_queue.get(timeout=1.0)
                orchestrator.on_event(event)
            except queue.Empty:
                pass
            now = time.monotonic()
            max_backlog = max(max_backlog, event_queue.qsize())
            if now - last_heartbeat >= 10:
                last_heartbeat = now
                heartbeat = {
                    "phase": args.phase, "alive": True, "time_ns": time.time_ns(),
                    "queue_backlog": event_queue.qsize(), "max_queue_backlog": max_backlog,
                    "worker_errors": len(worker_errors), "reconnects": sum(reconnects.values()),
                    "counts": dict(orchestrator.counts),
                }
                path = run_root / "health/heartbeat.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                orchestrator.recorder.sync()
                path.write_text(json.dumps(heartbeat, indent=2) + "\n")
    finally:
        stop.set()
        for thread in threads: thread.join(timeout=65)
    while not event_queue.empty():
        orchestrator.on_event(event_queue.get_nowait())
    ended_ns = time.time_ns()
    orchestrator.flush(ended_ns)
    summary = orchestrator.write_outputs(started_ns, ended_ns, args.phase)
    orchestrator.recorder.close()
    summary.update({"max_queue_backlog": max_backlog, "worker_errors": worker_errors, "reconnects": reconnects})
    status = "PASSED" if orchestrator.counts["quote_events"] and orchestrator.counts["trade_events"] else "BLOCKED"
    validation = {
        "status": status, "phase": args.phase, "manifest_hash": expected,
        "candidate_count": len(active), "manifest_symbols": symbols,
        "subscribed_symbols": symbols, "symbol_subscription_mismatch": 0,
        "production_market_data": "READ_ONLY", "production_trading_client": "NOT_INITIALIZED",
        "production_exchange_orders": 0, "lookahead_events": 0,
        "summary": summary,
    }
    (run_root / "dry_run_validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    print(json.dumps(validation, indent=2))
    return 0 if status == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
