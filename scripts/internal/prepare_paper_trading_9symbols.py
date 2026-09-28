#!/usr/bin/env python3
"""Freeze the nine-symbol forward-paper manifest from finalized history.

This command is deliberately pre-forward only.  It never opens a market-data
connection and has no exchange-order surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from shutil import which

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_framework.registry import get_entry  # noqa: E402


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def git_commit(repo: Path) -> str:
    override = os.environ.get("CODE_COMMIT_OVERRIDE")
    if override:
        return override.strip()
    executable = which("git")
    if executable is None:
        return "UNAVAILABLE"
    return subprocess.check_output([executable, "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def truth(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def selection_rule(timeframe: str) -> str:
    if timeframe == "1m":
        return "abs(FIRST_TICK_Sharpe)>1.5"
    return "abs(FIRST_TICK_Signed_BE_bps)>10 AND abs(FIRST_TICK_Sharpe)>1.0"


def retire_old_experiment(path: Path) -> None:
    if not path.exists():
        return
    marker = {
        "experiment_id": path.name,
        "status": "NOT_STARTED",
        "forward_data_used": False,
        "forward_data_status": "NO_FORWARD_DATA_USED",
        "supersession_status": "SUPERSEDED_BEFORE_FORWARD_START",
        "preserved_as": "superseded_pre_9symbol_manifest",
    }
    target = path / "manifest" / "superseded_pre_9symbol_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--classification", type=Path)
    parser.add_argument("--paper-root", type=Path)
    parser.add_argument("--old-experiment", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    classification = (args.classification or repo / "outputs/deliverables/multi_symbol_classification/multi_symbol_classification_master.csv").resolve()
    paper_root = (args.paper_root or repo / "paper_trading").resolve()
    old = (args.old_experiment or paper_root / "experiments/paper_20260928_e84ad47980d5").resolve()

    source = pd.read_csv(classification)
    source["selected"] = truth(source["selected"])
    source["reverse_candidate"] = truth(source["reverse_candidate"])
    selected = source.loc[source.selected].copy()
    if selected.empty or selected.symbol.nunique() <= 1:
        raise ValueError("nine-symbol candidate lineage failed: selected universe has <=1 symbol")
    if selected.duplicated(["strategy_id", "symbol", "timeframe"]).any():
        raise ValueError("historical classification contains duplicate logical cases")
    if (source.reverse_candidate & ~source.selected).any():
        raise ValueError("historical STRICT_REVERSE candidates are not a subset of selected NORMAL cases")

    rows: list[dict[str, object]] = []
    hash_cache: dict[str, tuple[str, str, str]] = {}
    for row in selected.sort_values(["strategy_id", "symbol", "timeframe"]).itertuples(index=False):
        sid = str(row.strategy_id)
        if sid not in hash_cache:
            entry = get_entry(sid)
            config_path = Path(entry.default_config_path)
            if not config_path.is_absolute():
                config_path = repo / config_path
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            parameter_hash = sha256_bytes(
                json.dumps(config.get("params", {}), sort_keys=True, separators=(",", ":"), default=str).encode()
            )
            hash_cache[sid] = (tree_hash(repo / "strategies" / sid), parameter_hash, sha256(config_path))
        strategy_hash, parameter_hash, config_hash = hash_cache[sid]
        variants = ["NORMAL"] + (["STRICT_REVERSE"] if bool(row.reverse_candidate) else [])
        for variant in variants:
            identity = f"{sid}|{row.symbol}|{row.timeframe}|{variant}"
            rows.append({
                "experiment_candidate_id": "pc_" + sha256_bytes(identity.encode())[:20],
                "strategy_id": sid,
                "semantic_group_id": row.semantic_group_id,
                "source_origin": row.source_origin,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "direction_variant": variant,
                "historical_selection_rule": selection_rule(str(row.timeframe)),
                "historical_start": row.effective_start,
                "historical_end": row.end,
                "historical_Return": row.Return,
                "historical_Sharpe": row.Sharpe,
                "historical_Signed_BE": row.Signed_BE,
                "historical_MaxDD": row.MaxDD,
                "historical_Avg_Daily_Turnover_pct": row.Avg_Daily_Turnover_pct,
                "strategy_code_hash": strategy_hash,
                "parameter_hash": parameter_hash,
                "config_hash": config_hash,
                "selected": True,
            })

    manifest = pd.DataFrame(rows).sort_values(
        ["strategy_id", "symbol", "timeframe", "direction_variant"]
    ).reset_index(drop=True)
    payload = manifest.to_csv(index=False, lineterminator="\n").encode()
    manifest_hash = sha256_bytes(payload)
    experiment_id = f"paper_{pd.Timestamp.now(tz='UTC'):%Y%m%d}_{manifest_hash[:12]}"
    experiment = paper_root / "experiments" / experiment_id
    if experiment.exists():
        raise FileExistsError(f"experiment already exists: {experiment}")
    manifest_dir = experiment / "manifest"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "paper_candidate_manifest_9symbols.csv"
    manifest_path.write_bytes(payload)
    (manifest_dir / "paper_candidate_manifest_9symbols.sha256").write_text(manifest_hash + "\n", encoding="utf-8")

    resolved = yaml.safe_load((repo / "configs/paper_trading_v1.yaml").read_text(encoding="utf-8"))
    resolved["experiment_id"] = experiment_id
    resolved["candidate_manifest"] = str(manifest_path)
    resolved["symbols"] = sorted(manifest.symbol.unique())
    resolved["candidate_count"] = len(manifest)
    config_path = manifest_dir / "paper_trading_v1.resolved.yaml"
    config_path.write_text(yaml.safe_dump(resolved, sort_keys=False), encoding="utf-8")
    audit = {
        "selected_base_logical_cases": int(len(selected)),
        "frozen_candidate_variants": int(len(manifest)),
        "unique_strategy_ids": int(manifest.strategy_id.nunique()),
        "unique_semantic_groups": int(manifest.semantic_group_id.nunique()),
        "unique_symbols": int(manifest.symbol.nunique()),
        "counts_by_symbol": manifest.groupby("symbol").size().sort_index().to_dict(),
        "counts_by_timeframe": manifest.groupby("timeframe").size().sort_index().to_dict(),
        "counts_by_source_origin": manifest.groupby("source_origin").size().sort_index().to_dict(),
        "counts_by_direction_variant": manifest.groupby("direction_variant").size().sort_index().to_dict(),
    }
    (manifest_dir / "manifest_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    freeze = {
        "experiment_id": experiment_id,
        "status": "FROZEN_PRE_START",
        "forward_start_timestamp": None,
        "candidate_manifest_hash": manifest_hash,
        "candidate_count": len(manifest),
        "base_logical_case_count": len(selected),
        "code_commit": git_commit(repo),
        "strategy_code_universe_hash": sha256_bytes("".join(sorted(manifest.strategy_code_hash.unique())).encode()),
        "paper_config_hash": sha256(config_path),
        "source_manifest": str(classification),
        "source_manifest_hash": sha256(classification),
        "market_data_environment": "PRODUCTION_READ_ONLY",
        "production_trading_client": "NOT_INITIALIZED",
        "order_submission": "DISABLED",
        "execution_modes": ["FIRST_TICK_SHADOW", "MAKER_PAPER"],
        "maker_policy": "GTC_UNTIL_SIGNAL_INVALID",
    }
    (manifest_dir / "paper_experiment_freeze.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(repo / "docs/paper_trading_protocol.md", manifest_dir / "paper_trading_protocol.md")
    for folder in [
        "market_data", "bars", "strategy_state", "decisions", "orders", "fills",
        "funding", "fees", "daily_metrics", "health", "figures", "audit_log",
    ]:
        (experiment / folder).mkdir()
    retire_old_experiment(old)
    print(json.dumps({"experiment_id": experiment_id, "manifest_hash": manifest_hash, **audit}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
