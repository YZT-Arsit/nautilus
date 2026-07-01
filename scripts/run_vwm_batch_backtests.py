#!/usr/bin/env python3
"""C1a-safe VWM batch backtest planning, inventory, and summary aggregation.

This module deliberately does not run large real backtests. The executable
surface for C1a is:

* inventory the local ``historical_data/market_data`` tree;
* parse and validate a VWM batch config;
* dry-run planned per-symbol jobs;
* aggregate already-written per-symbol reports.

The real execution branch is blocked until C1b/C1c intentionally wires it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import statistics
import sys
import time
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any


_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.historical import LocalDataCatalog  # noqa: E402
from data_engine.historical.catalog import partition_dir  # noqa: E402


REQUIRED_BAR_COLUMNS = ("open", "high", "low", "close", "volume")
OPTIONAL_TIME_COLUMNS = ("event_time_ns", "ts")
INVENTORY_COLUMNS = (
    "exchange",
    "venue_type",
    "symbol",
    "bar_type",
    "first_date",
    "last_date",
    "num_days",
    "num_partitions",
    "estimated_rows",
    "file_size_mb",
    "required_columns_present",
    "status",
    "notes",
)
NATIVE_CATALOG_COLUMNS = (
    "catalog",
    "data_type",
    "symbol",
    "first_time",
    "last_time",
    "num_files",
    "estimated_rows",
    "file_size_mb",
    "required_columns_present",
    "vwm_bar_candidate",
    "status",
    "notes",
)
SUMMARY_COLUMNS = (
    "exchange",
    "venue_type",
    "symbol",
    "bar_type",
    "start",
    "end",
    "num_bars",
    "strategy",
    "params_hash",
    "initial_cash",
    "final_equity",
    "total_return",
    "annualized_return",
    "gross_realized_pnl",
    "net_pnl",
    "max_drawdown",
    "max_drawdown_pct",
    "volatility",
    "sharpe",
    "sortino",
    "trade_count",
    "fill_count",
    "long_trade_count",
    "short_trade_count",
    "win_rate",
    "avg_trade_pnl",
    "avg_win",
    "avg_loss",
    "profit_factor",
    "turnover",
    "total_commission",
    "commission_to_gross_pnl",
    "commission_to_equity",
    "avg_commission_per_trade",
    "status",
    "error_type",
    "error_message",
    "elapsed_sec",
    "job_id",
    "exit_code",
    "stdout_tail",
    "stderr_tail",
    "output_dir",
    "rank_total_return",
    "rank_max_drawdown",
    "rank_profit_factor",
    "rank_sharpe",
    "rank_net_score",
)
FAILURE_COLUMNS = (
    "exchange",
    "venue_type",
    "symbol",
    "bar_type",
    "start",
    "end",
    "job_id",
    "status",
    "exit_code",
    "error_type",
    "error_message",
    "stdout_tail",
    "stderr_tail",
    "elapsed_sec",
    "output_dir",
)


@dataclass(frozen=True)
class InventoryRow:
    exchange: str
    venue_type: str
    symbol: str
    bar_type: str
    first_date: str
    last_date: str
    num_days: int
    num_partitions: int
    estimated_rows: int | None
    file_size_mb: float
    required_columns_present: bool
    status: str
    notes: str


@dataclass(frozen=True)
class BatchJob:
    exchange: str
    venue_type: str
    symbol: str
    bar_type: str
    start: str
    end: str
    output_dir: str
    strategy: str
    params_hash: str
    instrument_id: str | None = None
    quantity: float | None = None


@dataclass(frozen=True)
class NativeCatalogRow:
    catalog: str
    data_type: str
    symbol: str
    first_time: str
    last_time: str
    num_files: int
    estimated_rows: int | None
    file_size_mb: float
    required_columns_present: bool
    vwm_bar_candidate: bool
    status: str
    notes: str


def parse_ymd(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"invalid {label} {value!r}; expected YYYY-MM-DD") from None


def daterange_days(start: str, end: str) -> int:
    first = parse_ymd(start, "start")
    last = parse_ymd(end, "end")
    if last < first:
        raise ValueError(f"end {end!r} is before start {start!r}")
    return (last - first).days + 1


def safe_output_root(path: str | Path, *, purpose: str) -> Path:
    out = Path(path)
    parts = out.parts
    allowed = {("outputs", "backtests"), ("outputs", "backtest_inventory")}
    if not any(parts[i : i + 2] in allowed for i in range(max(0, len(parts) - 1))):
        raise ValueError(
            f"{purpose} output root must live under outputs/backtests or "
            f"outputs/backtest_inventory, got {str(path)!r}"
        )
    return out


def _ensure_smoke_output_root(path: Path) -> None:
    allowed_names = (
        "vwm_batch_smoke",
        "cffex_vwm_midbar_smoke",
        "crypto_perpetual_vwm_smoke",
        "crypto_perpetual_multisymbol_vwm_smoke",
        "vwm_btcusdt_perpetual",
        "vwm_crypto_perpetual",
        "vwm_binance_um",
    )
    if not path.name.startswith(allowed_names):
        raise ValueError(
            "real smoke execution is restricted to approved smoke output roots"
        )
    if path.exists():
        raise FileExistsError(f"output root already exists, refusing to overwrite: {path}")


def _params_hash(params: dict[str, Any]) -> str:
    payload = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _read_parquet_metadata(path: Path) -> tuple[int | None, set[str]]:
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415

        meta = pq.read_metadata(path)
        return int(meta.num_rows), set(meta.schema.names)
    except Exception:
        return None, set()


def _parse_native_time_range(path: Path) -> tuple[str, str]:
    stem = path.stem
    if "_" not in stem:
        return "", ""
    first, last = stem.split("_", 1)
    return first, last


def scan_native_catalog(root: str | Path, *, read_metadata: bool = True) -> list[NativeCatalogRow]:
    """Scan a Nautilus catalog tree such as ``catalog/data/quote_tick/SYMBOL/*.parquet``.

    Native quote/depth/contract data is useful inventory context, but it is not
    directly a VWM bar candidate unless a future data_type contains OHLCV bar
    columns.
    """
    root = Path(root)
    groups: dict[tuple[str, str, str], list[Path]] = {}
    if not root.exists():
        return []
    for file in root.rglob("*.parquet"):
        rel = file.relative_to(root).parts
        if len(rel) < 5 or rel[1] != "data":
            continue
        catalog, data_type, symbol = rel[0], rel[2], rel[3]
        groups.setdefault((catalog, data_type, symbol), []).append(file)

    rows: list[NativeCatalogRow] = []
    for (catalog, data_type, symbol), files in sorted(groups.items()):
        times = [_parse_native_time_range(f) for f in files]
        starts = sorted(t[0] for t in times if t[0])
        ends = sorted(t[1] for t in times if t[1])
        estimated_rows = 0
        saw_rows = False
        schema_names: set[str] = set()
        if read_metadata:
            for file in files:
                rows_for_file, names = _read_parquet_metadata(file)
                schema_names.update(names)
                if rows_for_file is not None:
                    estimated_rows += rows_for_file
                    saw_rows = True

        is_bar_type = data_type in {"bar", "bars", "bar_data", "bar_type"}
        required_ok = bool(schema_names) and all(c in schema_names for c in REQUIRED_BAR_COLUMNS)
        required_ok = required_ok and any(c in schema_names for c in OPTIONAL_TIME_COLUMNS)
        vwm_candidate = is_bar_type and required_ok
        notes: list[str] = []
        if data_type in {"quote_tick", "order_book_depths", "futures_contract"}:
            notes.append("native catalog data is not directly OHLCV bars")
        if read_metadata and not schema_names:
            notes.append("parquet metadata unavailable")
        if read_metadata and is_bar_type and not required_ok:
            notes.append("bar-like data missing required OHLCV/time columns")
        status = "usable" if vwm_candidate else "not_bar_data"
        rows.append(
            NativeCatalogRow(
                catalog=catalog,
                data_type=data_type,
                symbol=symbol,
                first_time=starts[0] if starts else "",
                last_time=ends[-1] if ends else "",
                num_files=len(files),
                estimated_rows=estimated_rows if saw_rows else None,
                file_size_mb=round(sum(f.stat().st_size for f in files) / 1_000_000, 6),
                required_columns_present=required_ok,
                vwm_bar_candidate=vwm_candidate,
                status=status,
                notes="; ".join(notes),
            )
        )
    return rows


def write_native_catalog_outputs(rows: list[NativeCatalogRow], output_root: str | Path) -> dict[str, str]:
    out = safe_output_root(output_root, purpose="native catalog inventory")
    out.mkdir(parents=True, exist_ok=True)
    dicts = [asdict(r) for r in rows]

    csv_path = out / "native_catalog_inventory.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=NATIVE_CATALOG_COLUMNS)
        writer.writeheader()
        writer.writerows(dicts)

    json_path = out / "native_catalog_inventory.json"
    json_path.write_text(json.dumps(dicts, indent=2), encoding="utf-8")

    md_path = out / "native_catalog_inventory.md"
    candidates = [r for r in rows if r.vwm_bar_candidate]
    lines = [
        "# Native Catalog Inventory",
        "",
        f"- rows: {len(rows)}",
        f"- vwm_bar_candidates: {len(candidates)}",
        "",
        "| catalog | data_type | symbol | first_time | last_time | files | rows | status |",
        "| --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.catalog} | {r.data_type} | {r.symbol} | {r.first_time} | "
            f"{r.last_time} | {r.num_files} | "
            f"{r.estimated_rows if r.estimated_rows is not None else ''} | {r.status} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path), "md": str(md_path)}


def _expected_days(first_date: str, last_date: str) -> int:
    return daterange_days(first_date, last_date)


def scan_vwm_inventory(
    root: str | Path,
    *,
    min_days: int = 2,
    read_metadata: bool = True,
) -> list[InventoryRow]:
    """Scan local bar partitions and summarize VWM candidate rows."""
    groups: dict[tuple[str, str, str, str], list[Any]] = {}
    for part in LocalDataCatalog(root).inventory():
        if part.data_kind != "bar":
            continue
        key = (part.exchange or "", part.venue_type or "", part.symbol or "", part.bar_type or "")
        groups.setdefault(key, []).append(part)

    rows: list[InventoryRow] = []
    for (exchange, venue_type, symbol, bar_type), parts in sorted(groups.items()):
        dates = sorted({p.date for p in parts if p.date})
        first_date = dates[0] if dates else ""
        last_date = dates[-1] if dates else ""
        file_size = sum(p.total_size_bytes for p in parts)
        estimated_rows = 0
        saw_row_count = False
        schema_names: set[str] = set()
        if read_metadata:
            for part in parts:
                for file in Path(part.path).glob("*.parquet"):
                    rows_for_file, names = _read_parquet_metadata(file)
                    schema_names.update(names)
                    if rows_for_file is not None:
                        estimated_rows += rows_for_file
                        saw_row_count = True
        required_ok = bool(schema_names) and all(c in schema_names for c in REQUIRED_BAR_COLUMNS)
        required_ok = required_ok and any(c in schema_names for c in OPTIONAL_TIME_COLUMNS)
        if not read_metadata:
            required_ok = True

        notes: list[str] = []
        if dates:
            expected = _expected_days(first_date, last_date)
            if expected != len(dates):
                notes.append(f"date gaps: expected {expected} days, found {len(dates)}")
        if read_metadata and not schema_names:
            notes.append("parquet metadata unavailable")
        if read_metadata and not required_ok:
            missing = [c for c in REQUIRED_BAR_COLUMNS if c not in schema_names]
            if not any(c in schema_names for c in OPTIONAL_TIME_COLUMNS):
                missing.append("event_time_ns|ts")
            notes.append(f"missing columns: {','.join(missing)}")

        if read_metadata and not required_ok:
            status = "missing_columns"
        elif len(dates) < min_days:
            status = "too_short"
        elif notes and notes[0].startswith("date gaps"):
            status = "incomplete"
        else:
            status = "usable"

        rows.append(
            InventoryRow(
                exchange=exchange,
                venue_type=venue_type,
                symbol=symbol,
                bar_type=bar_type,
                first_date=first_date,
                last_date=last_date,
                num_days=len(dates),
                num_partitions=len(parts),
                estimated_rows=estimated_rows if saw_row_count else None,
                file_size_mb=round(file_size / 1_000_000, 6),
                required_columns_present=required_ok,
                status=status,
                notes="; ".join(notes),
            )
        )
    return rows


def write_inventory_outputs(rows: list[InventoryRow], output_root: str | Path) -> dict[str, str]:
    out = safe_output_root(output_root, purpose="inventory")
    out.mkdir(parents=True, exist_ok=True)
    dicts = [asdict(r) for r in rows]

    csv_path = out / "vwm_candidate_inventory.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=INVENTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(dicts)

    json_path = out / "vwm_candidate_inventory.json"
    json_path.write_text(json.dumps(dicts, indent=2), encoding="utf-8")

    md_path = out / "README.md"
    usable = [r for r in rows if r.status == "usable"]
    exchanges = sorted({r.exchange for r in rows})
    venue_types = sorted({r.venue_type for r in rows})
    crypto_like = [r for r in rows if r.symbol.endswith(("USDT", "USD", "BTC", "ETH"))]
    lines = [
        "# VWM Candidate Inventory",
        "",
        f"- rows: {len(rows)}",
        f"- usable: {len(usable)}",
        f"- exchanges: {', '.join(exchanges) if exchanges else 'none'}",
        f"- venue_types: {', '.join(venue_types) if venue_types else 'none'}",
        f"- non_crypto_detected: {'yes' if rows and len(crypto_like) < len(rows) else 'no'}",
        "",
        "| exchange | venue_type | symbol | bar_type | first | last | days | rows | status |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.exchange} | {r.venue_type} | {r.symbol} | {r.bar_type} | "
            f"{r.first_date} | {r.last_date} | {r.num_days} | "
            f"{r.estimated_rows if r.estimated_rows is not None else ''} | {r.status} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path), "md": str(md_path)}


def load_batch_config(path: str | Path) -> dict[str, Any]:
    import yaml  # noqa: PLC0415

    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(cfg, dict):
        raise ValueError("batch config must be a mapping")
    return cfg


def validate_batch_config(cfg: dict[str, Any]) -> None:
    strategy = cfg.get("strategy") or {}
    if strategy.get("name") != "vwm":
        raise ValueError("strategy.name must be 'vwm' for this C1 batch runner")
    data = cfg.get("data") or {}
    if not data.get("root"):
        raise ValueError("data.root is required")
    start = str(data.get("start") or "")
    end = str(data.get("end") or "")
    daterange_days(start, end)
    universe = cfg.get("universe") or {}
    include = universe.get("include") or []
    if not isinstance(include, list):
        raise ValueError("universe.include must be a list")
    for i, item in enumerate(include):
        missing = [k for k in ("exchange", "venue_type", "symbol") if k not in item]
        if missing:
            raise ValueError(f"universe.include[{i}] missing {missing}")
    output = cfg.get("output") or {}
    safe_output_root(output.get("root", "outputs/backtests/vwm_batch"), purpose="batch")


def _candidate_from_inventory(row: InventoryRow) -> dict[str, str]:
    return {
        "exchange": row.exchange,
        "venue_type": row.venue_type,
        "symbol": row.symbol,
        "bar_type": row.bar_type,
    }


def _opt_float(value: Any) -> float | None:
    """Coerce an optional per-job quantity to float; None when absent/blank."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_jobs(
    cfg: dict[str, Any],
    *,
    inventory_rows: list[InventoryRow] | None = None,
    max_symbols: int | None = None,
    start: str | None = None,
    end: str | None = None,
    bar_type: str | None = None,
) -> list[BatchJob]:
    validate_batch_config(cfg)
    strategy = cfg["strategy"]["name"]
    params = cfg["strategy"].get("params") or {}
    phash = _params_hash(params)
    data = cfg.get("data") or {}
    output = cfg.get("output") or {}
    output_root = safe_output_root(output.get("root", "outputs/backtests/vwm_batch"), purpose="batch")
    run_start = start or str(data["start"])
    run_end = end or str(data["end"])
    daterange_days(run_start, run_end)
    selected_bar_type = bar_type or data.get("bar_type")

    include = list((cfg.get("universe") or {}).get("include") or [])
    if not include and inventory_rows is not None:
        include = [_candidate_from_inventory(r) for r in inventory_rows if r.status == "usable"]
    jobs: list[BatchJob] = []
    for item in include:
        job_bar_type = item.get("bar_type") or selected_bar_type
        if selected_bar_type and job_bar_type != selected_bar_type:
            continue
        stem = (
            f"{item['exchange']}_{item['venue_type']}_{item['symbol']}_{job_bar_type}_"
            f"{run_start.replace('-', '')}_{run_end.replace('-', '')}"
        )
        jobs.append(
            BatchJob(
                exchange=str(item["exchange"]),
                venue_type=str(item["venue_type"]),
                symbol=str(item["symbol"]),
                bar_type=str(job_bar_type),
                start=run_start,
                end=run_end,
                output_dir=str(output_root / stem),
                strategy=strategy,
                params_hash=phash,
                instrument_id=item.get("instrument_id"),
                quantity=_opt_float(item.get("quantity", item.get("order_quantity"))),
            )
        )
        if max_symbols is not None and len(jobs) >= max_symbols:
            break
    return jobs


def _resolve_repo_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _REPO / p


def _bar_partitions_present(
    *,
    root: str | Path,
    exchange: str,
    venue_type: str,
    symbol: str,
    bar_type: str,
    start: str,
    end: str,
) -> tuple[bool, list[str]]:
    first = parse_ymd(start, "start")
    last = parse_ymd(end, "end")
    missing: list[str] = []
    day = first
    from datetime import timedelta

    while day <= last:
        day_s = day.isoformat()
        pdir = partition_dir(
            root,
            exchange=exchange,
            venue_type=venue_type,
            symbol=symbol,
            data_kind="bar",
            bar_type=bar_type,
            date=day_s,
        )
        if not pdir.exists() or not any(pdir.glob("*.parquet")):
            missing.append(day_s)
        day += timedelta(days=1)
    return not missing, missing


def _strategy_params_for_run(cfg: dict[str, Any], job: BatchJob) -> dict[str, Any]:
    params = dict((cfg.get("strategy") or {}).get("params") or {})
    if "atr_pct" in params and "atr_pcnt" not in params:
        params["atr_pcnt"] = params.pop("atr_pct")
    params.setdefault("instrument_id", job.instrument_id or f"{job.symbol}.{job.exchange}")
    params["bar_type"] = job.bar_type
    return params


def _resolved_strategy_config(cfg: dict[str, Any], job: BatchJob, output_root: Path) -> dict[str, Any]:
    execution = dict(cfg.get("execution") or {})
    execution["backend"] = "nautilus_backtest"
    execution.setdefault("mode", "nautilus_native")
    execution.setdefault("initial_cash", 100000)
    execution.setdefault("quantity", 1.0)
    execution.setdefault("sell_means", "short")
    execution.setdefault("allow_short", True)
    execution.setdefault("price_field", "close")
    execution.setdefault("fee_rate", 0.0005)
    execution.setdefault("slippage_bps", 1.0)
    execution.setdefault("fill_timing", "same_bar")
    if job.quantity is not None:                     # per-job notional-normalized size
        execution["quantity"] = job.quantity
    data = dict(cfg.get("data") or {})
    instrument_id = job.instrument_id or f"{job.symbol}.{job.exchange}"
    return {
        "run_name": Path(job.output_dir).name,
        "strategy": "vwm_short",
        "params": _strategy_params_for_run(cfg, job),
        "data": {
            "mode": "hive_parquet_bars",
            "root": data.get("root", "historical_data/market_data"),
            "instrument_id": instrument_id,
            "warmup_bars": 0,
            "timestamp_column": data.get("timestamp_column", "ts"),
            "timestamp_unit": data.get("timestamp_unit", "ns"),
            "open_column": data.get("open_column", "open"),
            "high_column": data.get("high_column", "high"),
            "low_column": data.get("low_column", "low"),
            "close_column": data.get("close_column", "close"),
            "volume_column": data.get("volume_column", "volume"),
            "filters": {
                "exchange": job.exchange,
                "venue_type": job.venue_type,
                "symbol": job.symbol,
                "bar_type": job.bar_type,
            },
            "start": job.start,
            "end": job.end,
        },
        "execution": execution,
        "output": {
            "root": str(output_root),
            "print_table": False,
        },
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    import yaml  # noqa: PLC0415

    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _tail(text: str | None, *, limit: int = 2000) -> str:
    text = text or ""
    return text[-limit:]


def _job_id(job: BatchJob) -> str:
    return Path(job.output_dir).name


def _write_failure(
    job: BatchJob,
    *,
    error_type: str,
    error_message: str,
    elapsed_sec: float,
    status: str = "failed",
    exit_code: int | None = None,
    stdout_tail: str = "",
    stderr_tail: str = "",
) -> None:
    out = Path(job.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metadata = {
        "identity": {
            "exchange": job.exchange,
            "venue_type": job.venue_type,
            "symbol": job.symbol,
            "bar_type": job.bar_type,
            "start": job.start,
            "end": job.end,
        },
        "strategy": job.strategy,
        "params_hash": job.params_hash,
        "job_id": _job_id(job),
        "status": status,
        "exit_code": exit_code,
        "error_type": error_type,
        "error_message": error_message,
        "stdout_tail": _tail(stdout_tail),
        "stderr_tail": _tail(stderr_tail),
        "elapsed_sec": round(elapsed_sec, 6),
    }
    (out / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _finalize_success(job: BatchJob, *, elapsed_sec: float, resolved_config: dict[str, Any]) -> None:
    out = Path(job.output_dir)
    metrics = _read_json_if_exists(out / "metrics.json")
    if metrics and not (out / "report.json").exists():
        (out / "report.json").write_text(json.dumps(metrics, indent=2, allow_nan=True), encoding="utf-8")
    config_resolved = out / "config_resolved.yaml"
    if not config_resolved.exists():
        _write_yaml(config_resolved, resolved_config)
    metadata = {
        "identity": {
            "exchange": job.exchange,
            "venue_type": job.venue_type,
            "symbol": job.symbol,
            "bar_type": job.bar_type,
            "start": job.start,
            "end": job.end,
        },
        "strategy": job.strategy,
        "params_hash": job.params_hash,
        "job_id": _job_id(job),
        "status": "success",
        "exit_code": 0,
        "elapsed_sec": round(elapsed_sec, 6),
    }
    (out / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _worker_command(job_config: Path, job_output_dir: Path) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_run-single-job",
        "--job-config",
        str(job_config),
        "--job-output-dir",
        str(job_output_dir),
    ]


def _run_single_job_worker(config_path: str | Path, job_output_dir: str | Path) -> int:
    from run_strategy import main as run_strategy_main  # noqa: PLC0415

    run_strategy_main(["--config", str(config_path)])
    return 0


def _run_job_subprocess(job: BatchJob, config_path: Path, *, timeout_sec: int | None = None):
    started = time.perf_counter()
    completed = subprocess.run(
        _worker_command(config_path, Path(job.output_dir)),
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        shell=False,
        timeout=timeout_sec,
    )
    return completed, time.perf_counter() - started


def run_batch_smoke(
    cfg: dict[str, Any],
    *,
    output_root: str | Path,
    max_symbols: int | None = None,
    fail_fast: bool = True,
    continue_on_error: bool = False,
    run_job_fn=None,
    start: str | None = None,
    end: str | None = None,
    bar_type: str | None = None,
) -> dict[str, Any]:
    out_root = safe_output_root(output_root, purpose="batch smoke")
    _ensure_smoke_output_root(out_root)
    jobs = build_jobs(cfg, max_symbols=max_symbols, start=start, end=end, bar_type=bar_type)
    if not jobs:
        raise ValueError("no smoke jobs selected")
    out_root.mkdir(parents=True, exist_ok=False)

    data_root = (cfg.get("data") or {}).get("root", "historical_data/market_data")
    started = time.perf_counter()
    failures = 0
    executed = 0
    for index, job in enumerate(jobs):
        job_started = time.perf_counter()
        job = BatchJob(**{**asdict(job), "output_dir": str(out_root / Path(job.output_dir).name)})
        ok, missing = _bar_partitions_present(
            root=_resolve_repo_path(data_root),
            exchange=job.exchange,
            venue_type=job.venue_type,
            symbol=job.symbol,
            bar_type=job.bar_type,
            start=job.start,
            end=job.end,
        )
        if not ok:
            _write_failure(
                job,
                error_type="missing_data",
                error_message=f"missing partitions: {','.join(missing)}",
                elapsed_sec=time.perf_counter() - job_started,
                exit_code=11,
            )
            failures += 1
            if fail_fast:
                for skipped in jobs[index + 1:]:
                    skipped = BatchJob(**{**asdict(skipped), "output_dir": str(out_root / Path(skipped.output_dir).name)})
                    _write_failure(
                        skipped,
                        status="not_run",
                        error_type="fail_fast_not_run",
                        error_message=f"previous job failed: {_job_id(job)}",
                        elapsed_sec=0.0,
                        exit_code=None,
                    )
                break
            continue
        resolved = _resolved_strategy_config(cfg, job, out_root)
        job_dir = Path(job.output_dir)
        job_dir.mkdir(parents=True, exist_ok=False)
        config_path = job_dir / "config_resolved.yaml"
        _write_yaml(config_path, resolved)
        runner = run_job_fn or _run_job_subprocess
        completed, elapsed = runner(job, config_path)
        executed += 1
        if completed.returncode == 0:
            _finalize_success(job, elapsed_sec=time.perf_counter() - job_started, resolved_config=resolved)
        else:
            _write_failure(
                job,
                error_type="subprocess_failed",
                error_message=f"subprocess exited with code {completed.returncode}",
                elapsed_sec=elapsed,
                exit_code=completed.returncode,
                stdout_tail=getattr(completed, "stdout", ""),
                stderr_tail=getattr(completed, "stderr", ""),
            )
            failures += 1
            if fail_fast:
                for skipped in jobs[index + 1:]:
                    skipped = BatchJob(**{**asdict(skipped), "output_dir": str(out_root / Path(skipped.output_dir).name)})
                    _write_failure(
                        skipped,
                        status="not_run",
                        error_type="fail_fast_not_run",
                        error_message=f"previous job failed: {_job_id(job)}",
                        elapsed_sec=0.0,
                        exit_code=None,
                    )
                break
            if not continue_on_error:
                break
    paths = aggregate_results(out_root)
    return {
        "jobs": len(jobs),
        "executed_jobs": executed,
        "failures": failures,
        "elapsed_sec": round(time.perf_counter() - started, 6),
        "output_root": str(out_root),
        "summary_paths": paths,
        "exit_code": 10 if failures else 0,
    }


def _nan() -> float:
    return float("nan")


def _metric(metrics: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in metrics:
            return metrics[name]
    return default


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_trade_sides(path: Path) -> tuple[int, int, list[float]]:
    if not path.exists():
        return 0, 0, []
    long_count = 0
    short_count = 0
    pnls: list[float] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            side = (row.get("side") or "").upper()
            if side == "LONG":
                long_count += 1
            elif side == "SHORT":
                short_count += 1
            try:
                pnls.append(float(row.get("realized_pnl") or "nan"))
            except ValueError:
                pass
    return long_count, short_count, pnls


def _profit_factor(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses == 0:
        return math.inf if wins > 0 else _nan()
    return wins / losses


def _infer_identity(run_dir: Path, metrics: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    identity = dict(metadata.get("identity") or {})
    if all(k in identity for k in ("exchange", "venue_type", "symbol", "bar_type")):
        return identity
    parts = run_dir.name.split("_")
    if len(parts) >= 6:
        identity.setdefault("exchange", parts[0])
        identity.setdefault("venue_type", parts[1])
        identity.setdefault("symbol", parts[2])
        identity.setdefault("bar_type", parts[3])
        identity.setdefault("start", parts[-2])
        identity.setdefault("end", parts[-1])
    identity.setdefault("start", metrics.get("start_time"))
    identity.setdefault("end", metrics.get("end_time"))
    return identity


def summarize_run_dir(run_dir: Path) -> dict[str, Any]:
    metrics = _read_json_if_exists(run_dir / "metrics.json") or _read_json_if_exists(run_dir / "report.json")
    metadata = _read_json_if_exists(run_dir / "run_metadata.json")
    identity = _infer_identity(run_dir, metrics, metadata)
    if not metrics:
        return {
            **{k: identity.get(k, "") for k in ("exchange", "venue_type", "symbol", "bar_type")},
            "start": identity.get("start", ""),
            "end": identity.get("end", ""),
            "strategy": metadata.get("strategy", "vwm"),
            "params_hash": metadata.get("params_hash", ""),
            "status": metadata.get("status", "failed"),
            "error_type": metadata.get("error_type", "missing_report"),
            "error_message": metadata.get("error_message", "metrics.json/report.json not found"),
            "elapsed_sec": metadata.get("elapsed_sec", ""),
            "job_id": metadata.get("job_id", run_dir.name),
            "exit_code": metadata.get("exit_code", ""),
            "stdout_tail": metadata.get("stdout_tail", ""),
            "stderr_tail": metadata.get("stderr_tail", ""),
            "output_dir": str(run_dir),
        }
    long_count, short_count, pnls = _read_trade_sides(run_dir / "trades.csv")
    trade_count = int(_metric(metrics, "trade_count", default=len(pnls) or 0) or 0)
    total_commission = float(_metric(metrics, "total_commission", default=0.0) or 0.0)
    gross_pnl = float(_metric(metrics, "gross_realized_pnl", "realized_pnl", default=0.0) or 0.0)
    final_equity = _metric(metrics, "final_equity", default=_nan())
    initial_cash = _metric(metrics, "initial_cash", default=_nan())
    profit_factor = _metric(metrics, "profit_factor", default=None)
    if profit_factor is None:
        profit_factor = _profit_factor(pnls)
    row = {
        "exchange": identity.get("exchange", ""),
        "venue_type": identity.get("venue_type", ""),
        "symbol": identity.get("symbol", ""),
        "bar_type": identity.get("bar_type", ""),
        "start": identity.get("start", metrics.get("start_time", "")),
        "end": identity.get("end", metrics.get("end_time", "")),
        "num_bars": _metric(metrics, "bar_count", "num_bars", default=""),
        "strategy": metadata.get("strategy", metrics.get("strategy", "vwm")),
        "params_hash": metadata.get("params_hash", metrics.get("params_hash", "")),
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "total_return": _metric(metrics, "total_return", default=_nan()),
        "annualized_return": _metric(metrics, "annualized_return", default=_nan()),
        "gross_realized_pnl": gross_pnl,
        "net_pnl": _metric(metrics, "net_pnl", default=_nan()),
        "max_drawdown": _metric(metrics, "max_drawdown", default=_nan()),
        "max_drawdown_pct": _metric(metrics, "max_drawdown_pct", "max_drawdown", default=_nan()),
        "volatility": _metric(metrics, "volatility", default=_nan()),
        "sharpe": _metric(metrics, "sharpe", default=_nan()),
        "sortino": _metric(metrics, "sortino", default=_nan()),
        "trade_count": trade_count,
        "fill_count": _metric(metrics, "fill_count", default=0),
        "long_trade_count": long_count,
        "short_trade_count": short_count,
        "win_rate": _metric(metrics, "win_rate", default=_nan()),
        "avg_trade_pnl": statistics.fmean(pnls) if pnls else _nan(),
        "avg_win": statistics.fmean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else _nan(),
        "avg_loss": statistics.fmean([p for p in pnls if p < 0]) if any(p < 0 for p in pnls) else _nan(),
        "profit_factor": profit_factor,
        "turnover": _metric(metrics, "turnover", default=_nan()),
        "total_commission": total_commission,
        "commission_to_gross_pnl": (total_commission / abs(gross_pnl)) if gross_pnl else _nan(),
        "commission_to_equity": (total_commission / final_equity) if final_equity else _nan(),
        "avg_commission_per_trade": (total_commission / trade_count) if trade_count else _nan(),
        "status": metadata.get("status", "success"),
        "error_type": metadata.get("error_type", ""),
        "error_message": metadata.get("error_message", ""),
        "elapsed_sec": metadata.get("elapsed_sec", ""),
        "job_id": metadata.get("job_id", run_dir.name),
        "exit_code": metadata.get("exit_code", 0),
        "stdout_tail": metadata.get("stdout_tail", ""),
        "stderr_tail": metadata.get("stderr_tail", ""),
        "output_dir": str(run_dir),
    }
    return row


def _rank(rows: list[dict[str, Any]], field: str, rank_field: str, *, reverse: bool) -> None:
    valid = []
    for row in rows:
        try:
            value = float(row.get(field))
        except (TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        valid.append((value, row))
    valid.sort(key=lambda item: item[0], reverse=reverse)
    for rank, (_value, row) in enumerate(valid, start=1):
        row[rank_field] = rank


def add_ranks(rows: list[dict[str, Any]]) -> None:
    _rank(rows, "total_return", "rank_total_return", reverse=True)
    _rank(rows, "max_drawdown_pct", "rank_max_drawdown", reverse=False)
    _rank(rows, "profit_factor", "rank_profit_factor", reverse=True)
    _rank(rows, "sharpe", "rank_sharpe", reverse=True)
    for row in rows:
        if row.get("status") != "success":
            row["net_score"] = _nan()
            continue
        try:
            row["net_score"] = float(row.get("total_return")) - 0.5 * abs(float(row.get("max_drawdown_pct")))
        except (TypeError, ValueError):
            row["net_score"] = _nan()
    _rank(rows, "net_score", "rank_net_score", reverse=True)
    for row in rows:
        for field in (
            "rank_total_return",
            "rank_max_drawdown",
            "rank_profit_factor",
            "rank_sharpe",
            "rank_net_score",
        ):
            row.setdefault(field, "")


def aggregate_results(output_root: str | Path) -> dict[str, str]:
    out = safe_output_root(output_root, purpose="summary")
    out.mkdir(parents=True, exist_ok=True)
    rows = [summarize_run_dir(p) for p in sorted(out.iterdir()) if p.is_dir()]
    add_ranks(rows)

    summary_csv = out / "summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    summary_json = out / "summary.json"
    summary_json.write_text(json.dumps(rows, indent=2, allow_nan=True), encoding="utf-8")

    failures = [r for r in rows if r.get("status") != "success"]
    failures_csv = out / "failures.csv"
    with failures_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FAILURE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(failures)

    summary_md = out / "summary.md"
    lines = [
        "# VWM Batch Summary",
        "",
        f"- runs: {len(rows)}",
        f"- failures: {len(failures)}",
        "",
        "| rank | symbol | bar_type | total_return | max_drawdown | profit_factor | status |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in sorted(rows, key=lambda r: r.get("rank_total_return") or 10**9):
        lines.append(
            f"| {row.get('rank_total_return', '')} | {row.get('symbol', '')} | "
            f"{row.get('bar_type', '')} | {row.get('total_return', '')} | "
            f"{row.get('max_drawdown_pct', '')} | {row.get('profit_factor', '')} | "
            f"{row.get('status', '')} |"
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "summary_csv": str(summary_csv),
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "failures_csv": str(failures_csv),
    }


def _print_jobs(jobs: list[BatchJob]) -> None:
    print(f"[dry-run] planned_jobs={len(jobs)}")
    for job in jobs:
        print(
            f"  {job.exchange}/{job.venue_type}/{job.symbol}/{job.bar_type} "
            f"{job.start}..{job.end} -> {job.output_dir}"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C1 VWM batch backtest planner/smoke runner")
    ap.add_argument("--config", default="configs/backtests/vwm_batch_candidates.yaml")
    ap.add_argument("--_run-single-job", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--job-config", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--job-output-dir", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--inventory", action="store_true", help="scan local market_data and write inventory outputs")
    ap.add_argument("--native-catalog-root", default=None, help="optional read-only Nautilus catalog root to inventory")
    ap.add_argument("--dry-run", action="store_true", help="print planned jobs without running backtests")
    ap.add_argument("--summary-only", action="store_true", help="aggregate existing per-symbol report dirs only")
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--out", default=None, help="override output.root")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--bar-type", default=None)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--continue-on-error", action="store_true")
    ap.add_argument("--fail-fast", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args(argv)

    if args._run_single_job:
        if not args.job_config or not args.job_output_dir:
            raise SystemExit("--_run-single-job requires --job-config and --job-output-dir")
        return _run_single_job_worker(args.job_config, args.job_output_dir)

    cfg = load_batch_config(args.config)
    if args.out:
        cfg.setdefault("output", {})["root"] = args.out
    validate_batch_config(cfg)
    output_root = safe_output_root((cfg.get("output") or {}).get("root", "outputs/backtests/vwm_batch"), purpose="batch")

    if args.inventory:
        t0 = time.perf_counter()
        rows = scan_vwm_inventory((cfg.get("data") or {}).get("root", "historical_data/market_data"))
        paths = write_inventory_outputs(rows, "outputs/backtest_inventory")
        print(f"[inventory] rows={len(rows)} elapsed_sec={time.perf_counter() - t0:.3f}")
        print(json.dumps(paths, indent=2))
        if args.native_catalog_root:
            native_rows = scan_native_catalog(args.native_catalog_root)
            native_paths = write_native_catalog_outputs(native_rows, "outputs/backtest_inventory")
            print(f"[native-catalog] rows={len(native_rows)} vwm_bar_candidates={sum(r.vwm_bar_candidate for r in native_rows)}")
            print(json.dumps(native_paths, indent=2))

    if args.summary_only:
        paths = aggregate_results(output_root)
        print(json.dumps(paths, indent=2))

    if args.dry_run:
        inventory_rows = scan_vwm_inventory((cfg.get("data") or {}).get("root", "historical_data/market_data"), read_metadata=False)
        jobs = build_jobs(
            cfg,
            inventory_rows=inventory_rows,
            max_symbols=args.max_symbols,
            start=args.start,
            end=args.end,
            bar_type=args.bar_type,
        )
        _print_jobs(jobs)
        return 0

    if not (args.inventory or args.summary_only):
        result = run_batch_smoke(
            cfg,
            output_root=output_root,
            max_symbols=args.max_symbols,
            fail_fast=args.fail_fast and not args.continue_on_error,
            continue_on_error=args.continue_on_error,
            start=args.start,
            end=args.end,
            bar_type=args.bar_type,
        )
        print(json.dumps(result, indent=2))
        return int(result.get("exit_code", 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
