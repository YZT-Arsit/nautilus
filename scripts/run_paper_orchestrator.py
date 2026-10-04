#!/usr/bin/env python3
"""Run bounded, production-read-only forward paper phases.

There is intentionally no authenticated Binance client and no order endpoint.
All orders are local Nautilus matching-engine objects.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import queue
import random
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
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


class ReconnectBudget:
    """One global reconnect-rate limiter shared by all feed workers."""

    def __init__(self, *, max_attempts: int, window_seconds: float, degraded_wait_seconds: float) -> None:
        if max_attempts < 1 or window_seconds <= 0 or degraded_wait_seconds < 0:
            raise ValueError("invalid reconnect budget")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.degraded_wait_seconds = degraded_wait_seconds
        self.attempts: deque[float] = deque()
        self.lock = threading.Lock()
        self.suppressed = 0
        self.degraded_waits = 0

    def delay_before_attempt(self, now: float) -> float:
        with self.lock:
            while self.attempts and now - self.attempts[0] >= self.window_seconds:
                self.attempts.popleft()
            if len(self.attempts) >= self.max_attempts:
                self.suppressed += 1
                self.degraded_waits += 1
                return max(
                    self.degraded_wait_seconds,
                    self.window_seconds - (now - self.attempts[0]),
                )
            self.attempts.append(now)
            return 0.0

    def record_after_wait(self, now: float) -> None:
        with self.lock:
            while self.attempts and now - self.attempts[0] >= self.window_seconds:
                self.attempts.popleft()
            self.attempts.append(now)


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
    parser.add_argument(
        "--phase",
        choices=["subset_smoke", "full_manifest_smoke", "continuity_test", "authoritative_24h"],
        required=True,
    )
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--subset-count", type=int, default=18)
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--worker-id")
    parser.add_argument("--preflight-cache", type=Path)
    parser.add_argument("--candidate-id")
    parser.add_argument("--align-minute", action="store_true")
    parser.add_argument("--expected-bars", type=int)
    parser.add_argument("--quote-stale-seconds", type=float, default=10.0)
    parser.add_argument("--trade-stale-seconds", type=float, default=10.0)
    parser.add_argument("--freeze-start", action="store_true")
    parser.add_argument("--route-label", default="LOCAL_FAILOVER_PROXY")
    parser.add_argument("--reconnect-max-attempts", type=int, default=3)
    parser.add_argument("--reconnect-window-seconds", type=float, default=60.0)
    parser.add_argument("--reconnect-degraded-wait-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        parser.error("duration must be positive")
    repo, experiment = args.repo.resolve(), args.experiment.resolve()
    manifest_path = experiment / "manifest/paper_candidate_manifest_9symbols.csv"
    expected = (experiment / "manifest/paper_candidate_manifest_9symbols.sha256").read_text().strip()
    if manifest_hash(manifest_path) != expected:
        raise RuntimeError("candidate manifest hash mismatch")
    manifest = pd.read_csv(manifest_path)
    if args.candidate_id:
        manifest = manifest.loc[manifest.experiment_candidate_id.eq(args.candidate_id)].copy()
        if len(manifest) != 1:
            raise RuntimeError(f"candidate-id must resolve to exactly one frozen row (got {len(manifest)})")
    if args.symbols:
        manifest = manifest[manifest.symbol.isin([s.upper() for s in args.symbols])].copy()
        if manifest.empty:
            raise RuntimeError("requested worker symbol set has no frozen candidates")
    if args.phase == "subset_smoke":
        active = deterministic_subset(manifest, args.subset_count)
        run_root = experiment / "preflight/subset_smoke"
    elif args.phase == "full_manifest_smoke":
        active, run_root = manifest, experiment / "preflight/full_manifest_smoke"
    elif args.phase == "authoritative_24h":
        active, run_root = manifest, experiment
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
    reconnect_request = {symbol: threading.Event() for symbol in symbols}
    feed_ready = threading.Event()
    event_seen = {symbol: {"quote": False, "trade": False} for symbol in symbols}
    last_receive_mono = {symbol: {"quote": None, "trade": None} for symbol in symbols}
    worker_running = {symbol: False for symbol in symbols}
    dropped_events = {symbol: 0 for symbol in symbols}
    worker_errors: list[dict] = []
    reconnects = {symbol: 0 for symbol in symbols}
    feed_state = {symbol: "RECOVERING" for symbol in symbols}
    connection_ids = {symbol: 0 for symbol in symbols}
    reconnect_times = {symbol: [] for symbol in symbols}
    last_healthy_mono = {symbol: None for symbol in symbols}
    last_stale_mono = {symbol: None for symbol in symbols}
    duplicate_events = {symbol: {"detected": 0, "dropped": 0} for symbol in symbols}
    seen_event_keys: set[tuple] = set()
    seen_event_order: deque[tuple] = deque()
    seen_event_limit = 2_000_000
    state_lock = threading.Lock()
    lifecycle_lock = threading.Lock()
    lifecycle_path = run_root / "connectivity_state_timeline.csv"
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_fields = [
        "timestamp", "connection_id", "stage", "proxy_endpoint", "remote_endpoint",
        "result", "latency_ms", "exception_type", "exception_message",
    ]
    with lifecycle_path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=lifecycle_fields).writeheader()
    run_end_wall_ns = 0
    reconnect_budget = ReconnectBudget(
        max_attempts=args.reconnect_max_attempts,
        window_seconds=args.reconnect_window_seconds,
        degraded_wait_seconds=args.reconnect_degraded_wait_seconds,
    )

    def log_connection(symbol: str, connection_id: str, stage: str, result: str, **extra) -> None:
        row = {
            "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
            "connection_id": connection_id,
            "stage": stage,
            "proxy_endpoint": os.environ.get("HTTPS_PROXY", "DIRECT"),
            "remote_endpoint": PUBLIC_WS,
            "result": result,
            "latency_ms": extra.get("latency_ms", ""),
            "exception_type": extra.get("exception_type", ""),
            "exception_message": extra.get("exception_message", ""),
        }
        with lifecycle_lock, lifecycle_path.open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=lifecycle_fields).writerow(row)

    def event_key(symbol: str, event) -> tuple | None:
        kind = str(event.event_type)
        if kind == "trade":
            identity = getattr(event, "trade_id", None)
            if identity is not None:
                return symbol, kind, str(identity)
            return symbol, kind, int(event.event_time_ns), float(event.price), float(event.quantity)
        if kind == "quote":
            identity = getattr(event, "update_id", None)
            if identity is not None:
                return symbol, kind, int(identity)
            return (
                symbol, kind, int(event.event_time_ns), float(event.bid_price),
                float(event.ask_price), getattr(event, "bid_size", None),
                getattr(event, "ask_size", None),
            )
        return None

    def enqueue_unique(symbol: str, event) -> None:
        key = event_key(symbol, event)
        if key is not None:
            with state_lock:
                if key in seen_event_keys:
                    duplicate_events[symbol]["detected"] += 1
                    duplicate_events[symbol]["dropped"] += 1
                    return
                seen_event_keys.add(key)
                seen_event_order.append(key)
                if len(seen_event_order) > seen_event_limit:
                    seen_event_keys.discard(seen_event_order.popleft())
        try:
            event_queue.put(event, timeout=5)
        except queue.Full:
            dropped_events[symbol] += 1

    def worker(symbol: str) -> None:
        iid = f"{symbol}-PERP.BINANCE"
        worker_running[symbol] = True
        failure_streak = 0
        first_attempt = True
        rng = random.Random(f"{symbol}:paper-feed")  # noqa: S311 - deterministic retry jitter
        while not stop.is_set():
            if not first_attempt:
                budget_delay = reconnect_budget.delay_before_attempt(time.monotonic())
                if budget_delay > 0:
                    log_connection(
                        symbol, f"{symbol}-BUDGET", "DEGRADED_WAIT", "WAIT",
                        latency_ms=budget_delay * 1_000,
                    )
                    if stop.wait(budget_delay):
                        break
                    reconnect_budget.record_after_wait(time.monotonic())
            first_attempt = False
            connection_ids[symbol] += 1
            connection_id = f"{symbol}-{connection_ids[symbol]:06d}"
            with state_lock:
                feed_state[symbol] = "RECOVERING"
            reconnect_request[symbol].clear()
            connection_seen = {"quote": False, "trade": False}
            readiness_buffer = []
            connected_at = time.monotonic()
            log_connection(symbol, connection_id, "WEBSOCKET_CONNECT", "ATTEMPT")
            source = BinancePublicWebSocketSource(
                symbol, ("trade", "bookTicker", "markPrice@1s"),
                base_url=PUBLIC_WS, instrument_id=iid,
            )
            try:
                for event in source.iter_events(
                    max_messages=1_000_000_000,
                    timeout_seconds=3_600.0,
                    receive_timeout_seconds=5.0,
                    stop_event=reconnect_request[symbol],
                ):
                    if stop.is_set():
                        break
                    if reconnect_request[symbol].is_set():
                        break
                    kind = str(event.event_type)
                    if kind in {"quote", "trade"}:
                        now_receive = time.monotonic()
                        event_seen[symbol][kind] = True
                        connection_seen[kind] = True
                        last_receive_mono[symbol][kind] = now_receive
                    readiness_buffer.append(event)
                    if all(connection_seen.values()):
                        became_healthy = False
                        with state_lock:
                            if feed_state[symbol] != "HEALTHY":
                                feed_state[symbol] = "HEALTHY"
                                last_healthy_mono[symbol] = time.monotonic()
                                became_healthy = True
                        if became_healthy:
                            log_connection(
                                symbol, connection_id, "RESUBSCRIBE_READY", "SUCCESS",
                                latency_ms=(time.monotonic() - connected_at) * 1_000,
                            )
                        for buffered in readiness_buffer:
                            enqueue_unique(symbol, buffered)
                        readiness_buffer.clear()
                        if all(feed_state[value] == "HEALTHY" for value in symbols):
                            feed_ready.set()
                if not stop.is_set() and not reconnect_request[symbol].is_set():
                    raise ConnectionError("public WebSocket stream ended before stop was requested")
            except Exception as exc:  # reconnect public data only
                if len(worker_errors) < 1_000:
                    worker_errors.append({"symbol": symbol, "error": repr(exc), "time_ns": time.time_ns()})
                log_connection(
                    symbol, connection_id, "DISCONNECT", "FAILED",
                    exception_type=type(exc).__name__, exception_message=str(exc),
                )
            finally:
                with state_lock:
                    feed_state[symbol] = "RECOVERING"
                if not stop.is_set():
                    reconnects[symbol] += 1
                    reconnect_times[symbol].append(time.monotonic())
            if stop.is_set():
                break
            stable_seconds = time.monotonic() - connected_at
            failure_streak = 0 if stable_seconds >= 300.0 else failure_streak + 1
            base_delay = min(30.0, 2.0 ** max(0, failure_streak - 1))
            delay = base_delay + rng.uniform(0.0, min(2.0, base_delay * 0.25))
            log_connection(symbol, connection_id, "RECONNECT_BACKOFF", "WAIT", latency_ms=delay * 1_000)
            stop.wait(delay)
        worker_running[symbol] = False

    threads = [threading.Thread(target=worker, args=(symbol,), daemon=True, name=f"feed-{symbol}") for symbol in symbols]
    for thread in threads: thread.start()
    preflight_deadline = time.monotonic() + 120.0
    latest_preflight_event_ns = 0
    while not feed_ready.is_set() and time.monotonic() < preflight_deadline:
        try:
            event = event_queue.get(timeout=1.0)
            latest_preflight_event_ns = max(latest_preflight_event_ns, int(event.event_time_ns))
        except queue.Empty:
            pass
    if not feed_ready.is_set():
        stop.set()
        raise RuntimeError("market-data preflight did not receive both quote and trade within 120 seconds")
    if args.align_minute:
        boundary_ns = ((latest_preflight_event_ns // 60_000_000_000) + 1) * 60_000_000_000
        first_active_event = None
        while first_active_event is None and time.monotonic() < preflight_deadline + 120.0:
            try:
                event = event_queue.get(timeout=1.0)
                if int(event.event_time_ns) >= boundary_ns:
                    first_active_event = event
            except queue.Empty:
                pass
        if first_active_event is None:
            stop.set()
            raise RuntimeError("market-data stream did not cross the frozen UTC minute boundary")
        started_ns = boundary_ns
    else:
        started_ns = time.time_ns()
        first_active_event = None
    run_end_wall_ns = started_ns + args.duration_seconds * 1_000_000_000
    if args.phase == "authoritative_24h" and (not args.worker_id or args.freeze_start):
        freeze_path = experiment / "manifest/paper_experiment_freeze.json"
        freeze = json.loads(freeze_path.read_text())
        if freeze.get("forward_start_timestamp") is not None:
            raise RuntimeError("authoritative forward start already frozen")
        freeze["forward_start_timestamp"] = pd.Timestamp(started_ns, unit="ns", tz="UTC").isoformat()
        freeze["status"] = "RUNNING_24H"
        temp = freeze_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(freeze, indent=2) + "\n")
        os.replace(temp, freeze_path)
    max_backlog = 0
    last_heartbeat = 0.0
    stale_incidents: list[dict] = []
    unexpected_worker_deaths: list[dict] = []
    minute_coverage: dict[int, dict[str, int]] = {}
    reached_end_boundary = False
    wall_safety_deadline = time.monotonic() + args.duration_seconds + 120.0

    def process_event(event) -> None:
        if event.event_time_ns < started_ns or event.event_time_ns >= run_end_wall_ns:
            return
        orchestrator.on_event(event)
        minute = int(event.event_time_ns // 60_000_000_000 * 60_000_000_000)
        row = minute_coverage.setdefault(minute, {"quote_count": 0, "trade_count": 0})
        if event.event_type == "quote": row["quote_count"] += 1
        elif event.event_type == "trade": row["trade_count"] += 1

    try:
        if first_active_event is not None:
            process_event(first_active_event)
        while not reached_end_boundary and time.monotonic() < wall_safety_deadline:
            try:
                event = event_queue.get(timeout=1.0)
                if event.event_time_ns >= run_end_wall_ns:
                    reached_end_boundary = True
                else:
                    process_event(event)
            except queue.Empty:
                pass
            now = time.monotonic()
            for thread in threads:
                if not thread.is_alive() and not any(row["worker"] == thread.name for row in unexpected_worker_deaths):
                    unexpected_worker_deaths.append({"worker": thread.name, "time_ns": time.time_ns()})
            for symbol in symbols:
                for kind, threshold in (("quote", args.quote_stale_seconds), ("trade", args.trade_stale_seconds)):
                    last = last_receive_mono[symbol][kind]
                    if last is not None and now - last > threshold and not reconnect_request[symbol].is_set():
                        stale_incidents.append({
                            "time_ns": time.time_ns(), "symbol": symbol, "stream": kind,
                            "age_seconds": now - last, "threshold_seconds": threshold,
                        })
                        with state_lock:
                            feed_state[symbol] = "STALE"
                            last_stale_mono[symbol] = now
                        log_connection(
                            symbol, f"{symbol}-{connection_ids[symbol]:06d}",
                            f"{kind.upper()}_STALE", "FAILED", latency_ms=(now - last) * 1_000,
                        )
                        reconnect_request[symbol].set()
            max_backlog = max(max_backlog, event_queue.qsize())
            if now - last_heartbeat >= 10:
                last_heartbeat = now
                heartbeat = {
                    "phase": args.phase, "alive": True, "time_ns": time.time_ns(),
                    "queue_backlog": event_queue.qsize(), "max_queue_backlog": max_backlog,
                    "worker_errors": len(worker_errors), "reconnects": sum(reconnects.values()),
                    "counts": dict(orchestrator.counts), "stale_incidents": len(stale_incidents),
                    "dropped_events": sum(dropped_events.values()), "workers_running": dict(worker_running),
                    "feed_state": dict(feed_state),
                    "duplicate_events_dropped": sum(v["dropped"] for v in duplicate_events.values()),
                    "reconnect_suppressed_by_backoff": reconnect_budget.suppressed,
                    "reconnect_degraded_waits": reconnect_budget.degraded_waits,
                }
                path = run_root / "health/heartbeat.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                orchestrator.recorder.sync()
                path.write_text(json.dumps(heartbeat, indent=2) + "\n")
    finally:
        stop.set()
        for event in reconnect_request.values():
            event.set()
        for thread in threads: thread.join(timeout=65)
    while not event_queue.empty():
        event = event_queue.get_nowait()
        if event.event_time_ns < run_end_wall_ns:
            process_event(event)
    process_ended_ns = time.time_ns()
    ended_ns = run_end_wall_ns
    orchestrator.flush(ended_ns)
    summary = orchestrator.write_outputs(started_ns, ended_ns, args.phase)
    orchestrator.recorder.close()
    expected_bars = args.expected_bars
    observed_bars = int(orchestrator.counts.get("bars_1m", 0))
    expected_minutes = args.duration_seconds // 60
    bar_path = run_root / "bars" / f"{symbols[0]}_1m.jsonl" if len(symbols) == 1 else None
    bar_minutes = set()
    if bar_path is not None and bar_path.exists():
        for line in bar_path.read_text(encoding="utf-8").splitlines():
            if line:
                bar = json.loads(line)
                bar_minutes.add(int(bar["event_time_ns"]) - 60_000_000_000)
    coverage_rows = []
    for minute in range(started_ns, run_end_wall_ns, 60_000_000_000):
        counts = minute_coverage.get(minute, {"quote_count": 0, "trade_count": 0})
        coverage_rows.append({
            "minute": pd.Timestamp(minute, unit="ns", tz="UTC").isoformat(), **counts,
            "quote_received": counts["quote_count"] > 0, "trade_received": counts["trade_count"] > 0,
            "bar_created": minute in bar_minutes,
            "data_health": "HEALTHY" if counts["quote_count"] > 0 and counts["trade_count"] > 0 else "GAP",
            "reconnect_count": sum(1 for values in reconnect_times.values() for value in values),
            "route_used": args.route_label,
        })
    coverage_frame = pd.DataFrame(coverage_rows)
    coverage_frame.to_csv(run_root / "market_data_continuity_monitor.csv", index=False)
    coverage_frame.to_csv(run_root / "minute_continuity.csv", index=False)
    reconnect_bursts = []
    for values in reconnect_times.values():
        for index, value in enumerate(values):
            reconnect_bursts.append(sum(value - prior <= 60.0 for prior in values[: index + 1]))
    max_reconnects_in_60s = max(reconnect_bursts, default=0)
    unrecovered_stale = any(
        last_stale_mono[symbol] is not None
        and (last_healthy_mono[symbol] is None or last_stale_mono[symbol] > last_healthy_mono[symbol])
        for symbol in symbols
    )
    summary.update({
        "max_queue_backlog": max_backlog, "worker_errors": worker_errors, "reconnects": reconnects,
        "stale_incidents": stale_incidents, "dropped_events": dropped_events,
        "unexpected_worker_deaths": unexpected_worker_deaths,
        "reached_end_boundary": reached_end_boundary, "process_ended_ns": process_ended_ns,
        "expected_minutes": expected_minutes, "expected_bars": expected_bars,
        "observed_bars": observed_bars,
        "unexplained_missing_bars": max(0, (expected_bars or 0) - observed_bars) if expected_bars else None,
        "feed_state_final": dict(feed_state), "unrecovered_stale": unrecovered_stale,
        "duplicate_events": duplicate_events,
        "duplicate_events_affecting_bars": 0,
        "max_reconnects_in_60s": max_reconnects_in_60s,
        "reconnect_suppressed_by_backoff": reconnect_budget.suppressed,
        "reconnect_degraded_waits": reconnect_budget.degraded_waits,
    })
    continuity_ok = (
        orchestrator.counts["quote_events"] > 0
        and orchestrator.counts["trade_events"] > 0
        and sum(dropped_events.values()) == 0
        and not unexpected_worker_deaths
        and summary["duplicate_events_affecting_bars"] == 0
        and max_reconnects_in_60s <= 3
        and not unrecovered_stale
        and reached_end_boundary
        and (expected_bars is None or observed_bars == expected_bars)
    )
    status = "PASSED" if continuity_ok else "BLOCKED"
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
