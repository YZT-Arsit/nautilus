#!/usr/bin/env python3
"""Run only the four missing dynamic_breakout_short 1m reverse cases.

The runner reconstructs the frozen NORMAL target path from each existing
authoritative review parquet, negates that target exactly, and reruns the
constant-notional execution/funding accounting.  It does not download data,
connect to an exchange, or derive reverse performance by negating PnL.

By default it refuses to start while the P0 paper gate is active.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_boss_multitimeframe_tick_screen import load_symbol  # noqa: E402
from scripts.internal.run_long_horizon_strict_reverse import (  # noqa: E402
    run_reverse_from_frozen_normal,
)


STRATEGY = "dynamic_breakout_short"
TIMEFRAME = "1m"
START = "2024-07-01"
END_EXCLUSIVE = "2026-06-30"
END_INCLUSIVE = "2026-06-29"
MISSING_SYMBOLS = ("ETHUSDT", "BNBUSDT", "ADAUSDT", "1000PEPEUSDT")
ACTIVE_P0_STATUSES = {
    "RUNNING_30M_NETWORK_SMOKE",
    "RUNNING_2H_CONTINUITY_TEST",
    "RUNNING_24H_CLEAN_AB",
}
MIN_FREE_RAM_GIB = 12.0
MAX_QUEUE_BACKLOG = 256
MAX_RECONNECTS = 1
MAX_HEARTBEAT_AGE_SECONDS = 30.0


def atomic_json(value: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n")
    os.replace(temporary, path)


def p0_gate(repo: Path) -> dict[str, object]:
    path = (
        repo / "outputs/baseline_evaluation/paper_market_data_continuity_repair/"
        "gate_status.json"
    )
    if not path.is_file():
        return {"status": "UNKNOWN"}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def p0_status(repo: Path) -> str:
    return str(p0_gate(repo).get("status", "UNKNOWN"))


def assert_p0_idle(repo: Path) -> str:
    status = p0_status(repo)
    if status in ACTIVE_P0_STATUSES or status.startswith("RUNNING_"):
        raise RuntimeError(f"P0_ACTIVE_REFUSING_P1_START: {status}")
    return status


def free_memory_gib() -> float:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        value = MemoryStatus()
        value.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            raise OSError("GlobalMemoryStatusEx failed")
        return float(value.avail_phys / 1024**3)
    pages = os.sysconf("SC_AVPHYS_PAGES")
    page_size = os.sysconf("SC_PAGE_SIZE")
    return float(pages * page_size / 1024**3)


def set_idle_process_priority() -> None:
    if os.name != "nt":
        return
    idle_priority_class = 0x00000040
    # Declare the pointer-sized Win32 handle explicitly.  Without a restype,
    # ctypes defaults to a 32-bit int and truncates the pseudo-handle on
    # 64-bit Windows, causing an otherwise valid SetPriorityClass call to
    # fail under the supervised task account.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetPriorityClass.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    if not kernel32.SetPriorityClass(handle, idle_priority_class):
        error = ctypes.get_last_error()
        raise OSError(error, "SetPriorityClass(IDLE) failed")


def heartbeat_path_for_gate(gate: dict[str, object]) -> Path | None:
    status = str(gate.get("status", "UNKNOWN"))
    if "30M" in status:
        keys = ("smoke_experiment",)
    elif "2H" in status:
        keys = ("continuity_experiment",)
    elif "24H" in status:
        keys = ("new_experiment", "clean_experiment", "experiment")
    else:
        keys = ()
    keys += (
        "current_experiment",
        "experiment",
        "new_experiment",
        "clean_experiment",
        "continuity_experiment",
        "smoke_experiment",
    )
    for key in keys:
        value = gate.get(key)
        if not isinstance(value, str) or not value:
            continue
        candidate = Path(value) / "workers/BTCUSDT/health/heartbeat.json"
        if candidate.is_file():
            return candidate
    return None


def p0_safety_audit(repo: Path) -> dict[str, object]:
    gate = p0_gate(repo)
    status = str(gate.get("status", "UNKNOWN"))
    active = status in ACTIVE_P0_STATUSES or status.startswith("RUNNING_")
    memory = free_memory_gib()
    result: dict[str, object] = {
        "checked_at_epoch_seconds": time.time(),
        "gate_status": status,
        "p0_active": active,
        "free_memory_gib": memory,
        "minimum_free_memory_gib": MIN_FREE_RAM_GIB,
        "max_queue_backlog": MAX_QUEUE_BACKLOG,
        "max_reconnects": MAX_RECONNECTS,
        "max_heartbeat_age_seconds": MAX_HEARTBEAT_AGE_SECONDS,
        "safe": True,
        "reasons": [],
    }
    if not active:
        return result
    heartbeat_path = heartbeat_path_for_gate(gate)
    result["heartbeat_path"] = str(heartbeat_path) if heartbeat_path else None
    if heartbeat_path is None:
        result["safe"] = False
        result["reasons"] = ["P0_HEARTBEAT_NOT_FOUND"]
        return result
    heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8-sig"))
    age = max(0.0, time.time() - heartbeat_path.stat().st_mtime)
    feed_state = heartbeat.get("feed_state", {})
    worker_state = heartbeat.get("workers_running", {})
    values = {
        "heartbeat_age_seconds": age,
        "alive": bool(heartbeat.get("alive", False)),
        "btc_feed_state": str(feed_state.get("BTCUSDT", "UNKNOWN")),
        "btc_worker_running": bool(worker_state.get("BTCUSDT", False)),
        "worker_errors": int(heartbeat.get("worker_errors", 0)),
        "reconnects": int(heartbeat.get("reconnects", 0)),
        "stale_incidents": int(heartbeat.get("stale_incidents", 0)),
        "dropped_events": int(heartbeat.get("dropped_events", 0)),
        "queue_backlog": int(heartbeat.get("queue_backlog", 0)),
    }
    result.update(values)
    reasons: list[str] = []
    if memory < MIN_FREE_RAM_GIB:
        reasons.append("FREE_MEMORY_BELOW_12_GIB")
    if not values["alive"]:
        reasons.append("P0_HEARTBEAT_NOT_ALIVE")
    if values["heartbeat_age_seconds"] > MAX_HEARTBEAT_AGE_SECONDS:
        reasons.append("P0_HEARTBEAT_STALE")
    if values["btc_feed_state"] != "HEALTHY":
        reasons.append("P0_FEED_NOT_HEALTHY")
    if not values["btc_worker_running"]:
        reasons.append("P0_WORKER_NOT_RUNNING")
    if values["worker_errors"] != 0:
        reasons.append("P0_WORKER_ERRORS_NONZERO")
    if values["reconnects"] > MAX_RECONNECTS:
        reasons.append("P0_RECONNECTS_ABOVE_LIMIT")
    if values["stale_incidents"] != 0:
        reasons.append("P0_STALE_INCIDENTS_NONZERO")
    if values["dropped_events"] != 0:
        reasons.append("P0_DROPPED_EVENTS_NONZERO")
    if values["queue_backlog"] > MAX_QUEUE_BACKLOG:
        reasons.append("P0_QUEUE_BACKLOG_ABOVE_LIMIT")
    result["reasons"] = reasons
    result["safe"] = not reasons
    return result


def start_safety(repo: Path, bounded_during_p0: bool) -> dict[str, object]:
    status = p0_status(repo)
    active = status in ACTIVE_P0_STATUSES or status.startswith("RUNNING_")
    if active and not bounded_during_p0:
        raise RuntimeError(f"P0_ACTIVE_REFUSING_P1_START: {status}")
    audit = p0_safety_audit(repo)
    if active and bounded_during_p0:
        set_idle_process_priority()
    return audit


def normal_case_root(repo: Path, symbol: str) -> Path:
    return (
        repo / "outputs/baseline_evaluation/tick_review_stageA_9symbols_preworkbook/"
        "matrix_cases" / f"symbol={symbol}" / f"timeframe={TIMEFRAME}"
        / f"strategy={STRATEGY}"
    )


def run(
    repo: Path,
    output: Path,
    market_root: Path,
    index_root: Path,
    *,
    bounded_during_p0: bool = False,
) -> dict[str, object]:
    safety = start_safety(repo, bounded_during_p0)
    gate_status = str(safety["gate_status"])
    progress_path = output / "reverse_rerun_progress.json"
    completed: list[str] = []
    skipped: list[str] = []
    safety_checks: list[dict[str, object]] = [safety]
    if not bool(safety["safe"]):
        result = {
            "status": "PAUSED_P0_UNSAFE",
            "p0_gate_status_at_start": gate_status,
            "bounded_during_p0": bounded_during_p0,
            "completed": completed,
            "skipped_existing": skipped,
            "pending": list(MISSING_SYMBOLS),
            "last_safety_check": safety,
            "downloads": 0,
            "production_orders": 0,
        }
        atomic_json(result, progress_path)
        return result
    atomic_json(
        {
            "status": "RUNNING",
            "p0_gate_status_at_start": gate_status,
            "strategy": STRATEGY,
            "timeframe": TIMEFRAME,
            "window_start": START,
            "window_end_exclusive": END_EXCLUSIVE,
            "symbols": list(MISSING_SYMBOLS),
            "completed": completed,
            "skipped_existing": skipped,
            "bounded_during_p0": bounded_during_p0,
            "safety_checks": safety_checks,
            "downloads": 0,
            "production_orders": 0,
        },
        progress_path,
    )
    for symbol in MISSING_SYMBOLS:
        symbol_safety = p0_safety_audit(repo)
        safety_checks.append(symbol_safety)
        if not bool(symbol_safety["safe"]):
            result = {
                "status": "PAUSED_P0_UNSAFE",
                "p0_gate_status_at_start": gate_status,
                "bounded_during_p0": bounded_during_p0,
                "completed": completed,
                "skipped_existing": skipped,
                "pending": [item for item in MISSING_SYMBOLS if item not in completed and item not in skipped],
                "last_safety_check": symbol_safety,
                "safety_checks": safety_checks,
                "downloads": 0,
                "production_orders": 0,
            }
            atomic_json(result, progress_path)
            return result
        source = normal_case_root(repo, symbol)
        summary_path = source / "summary.json"
        review_path = source / "review_timeseries.parquet"
        if not summary_path.is_file() or not review_path.is_file():
            raise FileNotFoundError(f"authoritative NORMAL source missing: {source}")
        destination = (
            output / "reverse_cases" / f"symbol={symbol}" / f"timeframe={TIMEFRAME}"
            / f"strategy={STRATEGY}"
        )
        result_path = destination / "summary.json"
        result_review = destination / "review_timeseries.parquet"
        if result_path.is_file() and result_review.is_file():
            saved = json.loads(result_path.read_text(encoding="utf-8-sig"))
            if (
                saved.get("status") == "COMPLETED"
                and saved.get("execution_variant") == "STRICT_REVERSE"
                and saved.get("strict_reverse_target_mismatch_count") == 0
            ):
                skipped.append(symbol)
                continue

        normal_summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        if normal_summary.get("status") != "COMPLETED":
            raise ValueError(f"NORMAL source is not complete: {summary_path}")
        bars, funding, _, tick_prices, waits = load_symbol(
            market_root, index_root, symbol, START, END_INCLUSIVE
        )
        row = SimpleNamespace(
            representative_strategy_id=STRATEGY,
            semantic_group_id=f"PRE_WORKBOOK:{STRATEGY}",
            source_origin="PRE_WORKBOOK",
            symbol=symbol,
            timeframe=TIMEFRAME,
        )
        summary, review = run_reverse_from_frozen_normal(
            row=row,
            members=[STRATEGY],
            normal_summary=normal_summary,
            normal_review_path=review_path,
            bars=bars,
            funding=funding,
            tick_prices=tick_prices,
            waits=waits,
        )
        summary.update(
            {
                "evaluation_start": START,
                "evaluation_end_exclusive": END_EXCLUSIVE,
                "source_normal_summary": str(summary_path),
                "source_normal_review": str(review_path),
                "rerun_scope": "P1_FOUR_MISSING_CASES_ONLY",
                "downloads": 0,
                "production_orders": 0,
            }
        )
        destination.mkdir(parents=True, exist_ok=True)
        atomic_json(summary, result_path)
        temporary = result_review.with_suffix(result_review.suffix + ".tmp")
        review.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, result_review)
        completed.append(symbol)
        atomic_json(
            {
                "status": "RUNNING",
                "p0_gate_status_at_start": gate_status,
                "strategy": STRATEGY,
                "timeframe": TIMEFRAME,
                "window_start": START,
                "window_end_exclusive": END_EXCLUSIVE,
                "symbols": list(MISSING_SYMBOLS),
                "completed": completed,
                "skipped_existing": skipped,
                "bounded_during_p0": bounded_during_p0,
                "safety_checks": safety_checks,
                "downloads": 0,
                "production_orders": 0,
            },
            progress_path,
        )
        del bars, funding, tick_prices, waits, review
        gc.collect()

    result = {
        "status": "PASSED",
        "p0_gate_status_at_start": gate_status,
        "strategy": STRATEGY,
        "timeframe": TIMEFRAME,
        "window_start": START,
        "window_end_exclusive": END_EXCLUSIVE,
        "requested_cases": len(MISSING_SYMBOLS),
        "completed": completed,
        "skipped_existing": skipped,
        "bounded_during_p0": bounded_during_p0,
        "safety_checks": safety_checks,
        "downloads": 0,
        "production_orders": 0,
        "strict_reverse_definition": "target_reverse(t)=-target_normal(t)",
        "pnl_derived_by_algebraic_negation": False,
    }
    atomic_json(result, progress_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--market-root", type=Path)
    parser.add_argument("--index-root", type=Path)
    parser.add_argument(
        "--bounded-during-p0",
        action="store_true",
        help=(
            "Allow serial IDLE-priority execution during an active P0 only while "
            "heartbeat, feed, queue, error, and free-memory gates remain healthy."
        ),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Report the current P0/resource safety gate without running any case.",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = (
        args.output
        or repo / "outputs/baseline_evaluation/dynamic_breakout_short_cross_symbol"
    ).resolve()
    market_root = (args.market_root or repo / "historical_data/market_data").resolve()
    index_root = (
        args.index_root
        or repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen/"
        "tick_execution_index"
    ).resolve()
    if args.check_only:
        audit = p0_safety_audit(repo)
        print(json.dumps(audit, indent=2, allow_nan=False))
        return 0 if bool(audit["safe"]) else 2
    print(
        json.dumps(
            run(
                repo,
                output,
                market_root,
                index_root,
                bounded_during_p0=args.bounded_during_p0,
            ),
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
