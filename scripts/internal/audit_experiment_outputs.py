#!/usr/bin/env python3
"""Inventory experiment artifacts without modifying their source trees."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def classify(relative: str) -> tuple[str, str]:
    normalized = relative.replace("\\", "/").lower()
    if normalized.endswith((".part", ".tmp")):
        return "obsolete_candidate", "interrupted temporary output"
    if normalized.startswith("batches/build_btcusdt_1s_5y/"):
        return "active_protected", "running five-year tick/bar standardization"
    if normalized.startswith("cache/binance_raw_trades_1s/"):
        return "active_protected", "input/cache used by active standardization"
    if normalized.startswith("batches/all_strategies_timeframe_lag/"):
        return "canonical", "current complete 1m/10m machine-readable results"
    if normalized.startswith("deliverables/current_strategy_results/"):
        return "canonical", "unified current reporting hierarchy"
    if normalized == "deliverables/current_strategy_results.zip":
        return "canonical", "portable archive of unified current reporting hierarchy"
    if "continuous_tick_ma" in normalized and "strategy_evaluation_7d_20210701" in normalized:
        return "canonical", "validated native-tick lag comparison"
    if "continuous_tick_ma" in normalized and "five_year_lag_60s" in normalized and "strict_reverse" not in normalized:
        return "canonical_pending_replacement", "only completed five-year native-tick source until lag comparison completes"
    if "strict_reverse" in normalized or "reverse_lag" in normalized:
        return "obsolete_candidate", "trivial executed-position sign inversion"
    if normalized.startswith(("archive/", "derived_market_data/", "ingestion_manifests/")):
        return "uncertain", "separate research or validation lineage; preserved"
    if normalized.startswith("batches/build_btcusdt_1s_5y_smoke"):
        return "uncertain", "raw-trade pipeline validation provenance; preserved"
    if "smoke" in normalized or normalized.startswith("chart_preview/"):
        return "obsolete_candidate", "debug/smoke output superseded by a complete run"
    if normalized.startswith("deliverables/all_strategies_timeframe_lag"):
        return "obsolete_candidate", "old reporting format superseded by unified figures"
    return "uncertain", "preserve until exact lineage or replacement is verified"


def value_after(parts: list[str], prefix: str) -> str:
    for part in parts:
        if part.lower().startswith(prefix):
            return part[len(prefix) :]
    return ""


def infer(relative: Path) -> dict[str, str]:
    parts = list(relative.parts)
    text = relative.as_posix()
    frequency = ""
    lag = ""
    for part in parts:
        lower = part.lower()
        if lower in {"1s", "5s", "15s", "30s", "1m", "10m", "tick"}:
            frequency = lower
        if "_lag" in lower:
            tail = lower.rsplit("_lag", 1)[1]
            lag = tail
            head = lower.rsplit("_lag", 1)[0]
            if head in {"1s", "5s", "15s", "30s", "1m", "10m", "tick"}:
                frequency = head
        elif lower.startswith("lag"):
            lag = lower.removeprefix("lag")
    strategy = ""
    for anchor in ("all_strategies_timeframe_lag", "batch_5y_futures"):
        if anchor in parts:
            index = parts.index(anchor)
            if index + 1 < len(parts):
                strategy = parts[index + 1]
    if "continuous_tick_ma" in parts:
        strategy = "continuous_tick_ma"
        frequency = frequency or "tick"
    premium = "included/excluded" if "timeseries.parquet" in text or "strategy_evaluation" in text else ""
    fee = "nofee" if "nofee" in parts else "fee_5bps" if "fee_5bps" in parts else ""
    return {
        "experiment": parts[1] if len(parts) > 1 else parts[0] if parts else "",
        "symbol": "BTCUSDT" if "BTCUSDT" in text.upper() or "btcusdt" in text.lower() else "",
        "frequency": frequency,
        "strategy": strategy,
        "lag": lag,
        "premium_mode": premium,
        "fee_mode": fee,
    }


def result_type(path: Path) -> str:
    name = path.name.lower()
    if path.suffix.lower() in {".png", ".svg", ".pdf"}:
        return "figure"
    if path.suffix.lower() in {".parquet", ".csv", ".json"}:
        return "machine_readable_result"
    if path.suffix.lower() in {".log", ".out"}:
        return "log"
    if name.endswith((".part", ".tmp")):
        return "temporary"
    if path.suffix.lower() in {".yaml", ".yml", ".toml"}:
        return "config"
    if path.suffix.lower() == ".zip":
        return "archive"
    return "other"


def main() -> int:
    args = parse_args()
    rows = []
    for path in sorted(item for item in args.root.rglob("*") if item.is_file()):
        relative = path.relative_to(args.root)
        classification, reason = classify(relative.as_posix())
        inferred = infer(relative)
        stat = path.stat()
        rows.append(
            {
                "path": str(path.resolve()),
                "machine": args.machine,
                **inferred,
                "modification_time_utc": datetime.fromtimestamp(
                    stat.st_mtime, tz=UTC
                ).isoformat(),
                "size_bytes": stat.st_size,
                "result_type": result_type(path),
                "classification": classification,
                "reason": reason,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["path"])
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(args.output)
    print(f"INVENTORY machine={args.machine} files={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
