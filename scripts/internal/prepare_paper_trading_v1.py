#!/usr/bin/env python3
"""Freeze the initial forward paper candidate manifest from completed history."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from shutil import which

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_framework.registry import get_entry  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*.py")):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(item.read_bytes())
    return digest.hexdigest()


def local_path(repo: Path, windows_path: str) -> Path:
    normalized = str(windows_path).replace("\\", "/")
    marker = "D:/nautilus/"
    if normalized.lower().startswith(marker.lower()):
        normalized = normalized[len(marker):]
    return repo / normalized


def avg_daily_turnover(review: Path, expected_total: float) -> tuple[float, int, float]:
    frame = pd.read_parquet(review, columns=["event_time_ns", "cumulative_turnover"])
    ts = pd.to_datetime(frame.event_time_ns, unit="ns", utc=True)
    midnight = ts.dt.hour.eq(0) & ts.dt.minute.eq(0) & ts.dt.second.eq(0)
    anchors = frame.loc[midnight, "cumulative_turnover"].to_numpy(float)
    values = np.diff(np.r_[anchors, float(frame.cumulative_turnover.iloc[-1])])
    residual = float(values.sum() + anchors[0] - expected_total)
    if abs(residual) > 1e-6:
        raise ValueError(f"turnover reconciliation failed for {review}: {residual}")
    return float(values.mean() * 100.0), len(values), residual


def git_commit(repo: Path) -> str:
    executable = which("git")
    if executable is None:
        raise RuntimeError("git executable unavailable")
    return subprocess.check_output(  # noqa: S603 - fixed executable from PATH, constant arguments
        [executable, "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--paper-root", type=Path)
    parser.add_argument("--design-root", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    source = (args.source_manifest or repo / "outputs/deliverables/strategy_four_mode_review/selection/long_horizon_first_tick_selected_cases.csv").resolve()
    paper_root = (args.paper_root or repo / "paper_trading").resolve()
    design = (args.design_root or repo / "outputs/baseline_evaluation/paper_trading_design").resolve()
    selected = pd.read_csv(source)
    if not selected.selected.astype(bool).all() or selected.duplicated(["strategy_id", "symbol", "timeframe"]).any():
        raise ValueError("source candidate manifest is not a unique frozen selected-case set")

    rows: list[dict[str, object]] = []
    for row in selected.sort_values(["strategy_id", "symbol", "timeframe"]).itertuples(index=False):
        entry = get_entry(str(row.strategy_id))
        config = repo / str(entry.default_config_path)
        package = repo / "strategies" / str(row.strategy_id)
        review = local_path(repo, str(row.normal_summary_path)).parent / "review_timeseries.parquet"
        avg_turnover, days, residual = avg_daily_turnover(review, float(row.Turnover_FIRST_TICK))
        rows.append({
            "strategy_id": row.strategy_id,
            "semantic_group_id": row.semantic_group_id,
            "source_origin": row.source_origin,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "direction_variant": "NORMAL",
            "historical_selection_reason": row.selection_rule,
            "historical_Return": row.Return_FIRST_TICK,
            "historical_Sharpe": row.Sharpe_FIRST_TICK,
            "historical_BE": row.Signed_BE_FIRST_TICK,
            "historical_Avg_Daily_Turnover": avg_turnover,
            "historical_turnover_days": days,
            "historical_turnover_reconciliation_mismatch": residual,
            "config_hash": sha256(config),
            "strategy_code_hash": tree_hash(package),
        })
    manifest = pd.DataFrame(rows)
    manifest_bytes = manifest.to_csv(index=False, lineterminator="\n").encode()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    experiment_id = f"paper_{pd.Timestamp.now(tz='UTC'):%Y%m%d}_{manifest_hash[:12]}"
    experiment = paper_root / "experiments" / experiment_id
    manifest_dir = experiment / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = manifest_dir / "paper_candidate_manifest.csv"
    manifest_path.write_bytes(manifest_bytes)
    (manifest_dir / "paper_candidate_manifest.sha256").write_text(manifest_hash + "\n", encoding="utf-8")

    config = yaml.safe_load((repo / "configs/paper_trading_v1.yaml").read_text(encoding="utf-8"))
    config["experiment_id"] = experiment_id
    config["candidate_manifest"] = str(manifest_path)
    config["candidate_count"] = len(manifest)
    config_path = manifest_dir / "paper_trading_v1.resolved.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    freeze = {
        "experiment_id": experiment_id,
        "forward_start_timestamp": None,
        "status": "FROZEN_PRE_START",
        "candidate_manifest_hash": manifest_hash,
        "candidate_count": len(manifest),
        "code_commit": git_commit(repo),
        "paper_config_hash": sha256(config_path),
        "source_manifest": str(source),
        "source_manifest_hash": sha256(source),
        "market_data_environment": "PRODUCTION_READ_ONLY",
        "order_submission": "DISABLED",
        "execution_modes": ["FIRST_TICK_SHADOW", "MAKER_PAPER"],
        "maker_policy": "GTC_UNTIL_SIGNAL_INVALID",
    }
    (manifest_dir / "paper_experiment_freeze.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    for folder in ["strategy_state", "orders", "fills", "daily_metrics", "figures", "audit_log"]:
        (experiment / folder).mkdir()

    design.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, design / "paper_candidate_manifest.csv")
    shutil.copy2(manifest_dir / "paper_candidate_manifest.sha256", design / "paper_candidate_manifest.sha256")
    shutil.copy2(config_path, design / "paper_trading_v1.resolved.yaml")
    shutil.copy2(manifest_dir / "paper_experiment_freeze.json", design / "paper_experiment_freeze.json")
    print(json.dumps({"experiment_id": experiment_id, "manifest": str(manifest_path), "hash": manifest_hash, "candidates": len(manifest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
