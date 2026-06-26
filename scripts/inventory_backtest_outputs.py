#!/usr/bin/env python3
"""Inventory + non-destructive cleanup plan for outputs/backtests.

Scans every directory under ``outputs/backtests``, classifies it (delivery /
reference / superseded / failed / other), and writes a CSV + Markdown inventory.
Cleanup only ever **moves** confidently-superseded dirs into an archive root
(never deletes); ``--dry-run-cleanup`` prints the plan without touching anything.
Pure stdlib: no network, no backtest, no strategy import.

Classification is name-driven (an explicit map for the known Phase-1 dirs) plus
content detection; unknown dirs default to ``manual_review`` and are never moved.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# name -> (experiment_type, recommended_action)
_CLASSIFY: dict[str, tuple[str, str]] = {
    # --- current Phase-1 delivery ---
    "vwm_crypto_perpetual_2026q2_15m_vol_targeted": ("vol_targeted_batch (recommended)", "keep_delivery"),
    "vwm_crypto_perpetual_2026q2_sizing_comparison": ("sizing_mode_comparison", "keep_delivery"),
    # --- Phase-1 reference (alternate sizing / filter / earlier) ---
    "vwm_crypto_perpetual_2026q2_15m_batch": ("fixed_quantity_batch", "keep_reference"),
    "vwm_crypto_perpetual_2026q2_15m_notional_normalized": ("notional_normalized_batch", "keep_reference"),
    "vwm_crypto_perpetual_2026q2_15m_vol_targeted_trend_filtered": ("trend_filtered_batch", "keep_reference"),
    "vwm_crypto_perpetual_2026q2_trend_filter_comparison": ("trend_filter_comparison", "keep_reference"),
    "vwm_btcusdt_perpetual_5m_eval": ("2024_single_eval (dev validation)", "keep_reference"),
    "vwm_btcusdt_perpetual_matrix": ("2024_matrix_aggregate", "keep_reference"),
    "vwm_btcusdt_perpetual_matrix_w7d": ("2024_matrix_window_source", "keep_reference"),
    "vwm_btcusdt_perpetual_matrix_w30d": ("2024_matrix_window_source", "keep_reference"),
    "vwm_btcusdt_perpetual_matrix_w90d": ("2024_matrix_window_source", "keep_reference"),
    "vwm_strategy_batch_eval": ("phase4_pivot (old rows=metric orientation)", "keep_reference"),
    # --- confidently superseded: runner-bug stale matrix dirs (replaced by _w*) ---
    "vwm_btcusdt_perpetual_matrix_7d": ("stale_matrix_runner_bug", "archive_superseded"),
    "vwm_btcusdt_perpetual_matrix_30d": ("stale_matrix_runner_bug", "archive_superseded"),
    "vwm_btcusdt_perpetual_matrix_90d": ("stale_matrix_runner_bug", "archive_superseded"),
}

_INVENTORY_COLUMNS = [
    "path", "modified_time", "experiment_type", "status", "contains_summary",
    "contains_failures", "contains_evaluation_table", "contains_job_dirs",
    "contains_equity_curve", "contains_fills_trades", "is_current_delivery",
    "is_superseded", "recommended_action",
]


def _has(d: Path, *names: str) -> bool:
    return any((d / n).is_file() for n in names)


def _job_dirs(d: Path) -> list[Path]:
    return [p for p in d.iterdir() if p.is_dir()]


def _failures_nonempty(d: Path) -> bool:
    f = d / "failures.csv"
    if not f.is_file():
        return False
    try:
        with f.open(encoding="utf-8") as fh:
            return sum(1 for _ in fh) > 1          # header + at least one row
    except Exception:
        return False


def classify(d: Path) -> dict[str, Any]:
    name = d.name
    exp_type, action = _CLASSIFY.get(name, ("other_track", "manual_review"))
    jobs = _job_dirs(d)
    has_equity = any((j / "equity_curve.csv").is_file() for j in jobs)
    has_ft = any((j / "trades.csv").is_file() or (j / "fills.csv").is_file() for j in jobs)
    has_eval = _has(d, "batch_evaluation_table.csv", "evaluation_table.csv",
                    "matrix_evaluation_table.csv", "sizing_mode_comparison.csv",
                    "trend_filter_comparison.csv")
    failures = _failures_nonempty(d)
    # Promote to archive_failed ONLY for recognized Phase-1 dirs that failed.
    # Unknown / other-track dirs stay manual_review (never auto-archived) -- the
    # conservative rule: if unsure, do not move.
    if failures and action not in ("keep_delivery", "keep_reference") and exp_type != "other_track":
        action = "archive_failed"
    try:
        mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        mtime = "NA"
    return {
        "path": str(d).replace("\\", "/"), "modified_time": mtime,
        "experiment_type": exp_type, "status": action.split("_")[0],
        "contains_summary": "yes" if _has(d, "summary.json") else "no",
        "contains_failures": "yes" if failures else "no",
        "contains_evaluation_table": "yes" if has_eval else "no",
        "contains_job_dirs": str(len(jobs)),
        "contains_equity_curve": "yes" if has_equity else "no",
        "contains_fills_trades": "yes" if has_ft else "no",
        "is_current_delivery": "yes" if action == "keep_delivery" else "no",
        "is_superseded": "yes" if action.startswith("archive") else "no",
        "recommended_action": action,
    }


def scan(backtests_root: Path) -> list[dict]:
    if not backtests_root.is_dir():
        return []
    rows = [classify(d) for d in sorted(backtests_root.iterdir()) if d.is_dir()]
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_INVENTORY_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in _INVENTORY_COLUMNS})


def write_doc(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_action: dict[str, list[dict]] = {}
    for r in rows:
        by_action.setdefault(r["recommended_action"], []).append(r)
    lines = ["# Phase 1 Outputs Inventory", "",
             "Non-destructive classification of `outputs/backtests/`. Cleanup only "
             "*moves* `archive_*` dirs to an archive root; `keep_*` and `manual_review` "
             "are never touched.", "",
             f"Total directories: {len(rows)}", ""]
    order = ["keep_delivery", "keep_reference", "archive_superseded", "archive_failed",
             "ignore_temp", "manual_review"]
    for act in order + [a for a in by_action if a not in order]:
        items = by_action.get(act, [])
        if not items:
            continue
        lines.append(f"## {act} ({len(items)})")
        lines.append("")
        lines.append("| dir | experiment_type | summary | eval_table | equity_curve | jobs |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in items:
            lines.append(f"| {Path(r['path']).name} | {r['experiment_type']} | "
                         f"{r['contains_summary']} | {r['contains_evaluation_table']} | "
                         f"{r['contains_equity_curve']} | {r['contains_job_dirs']} |")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cleanup_plan(rows: list[dict], archive_root: Path) -> list[dict]:
    plan = []
    for r in rows:
        if r["recommended_action"] in ("archive_superseded", "archive_failed"):
            old = Path(r["path"])
            plan.append({"old_path": str(old).replace("\\", "/"),
                         "new_path": str(archive_root / old.name).replace("\\", "/"),
                         "reason": r["experiment_type"], "recommended_action": r["recommended_action"]})
    return plan


def apply_cleanup(plan: list[dict], archive_root: Path, *, now_iso: str) -> list[dict]:
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for p in plan:
        old, new = Path(p["old_path"]), Path(p["new_path"])
        moved = "no"
        note = ""
        if not old.exists():
            note = "source missing, skipped"
        elif new.exists():
            note = "destination exists, skipped (no overwrite)"
        else:
            shutil.move(str(old), str(new))
            moved = "yes"
        manifest.append({"old_path": p["old_path"], "new_path": p["new_path"],
                         "reason": p["reason"], "moved_at": now_iso if moved == "yes" else "",
                         "reversible": "yes", "moved": moved, "notes": note})
    return manifest


def write_manifest(manifest: list[dict], path: Path) -> None:
    cols = ["old_path", "new_path", "reason", "moved_at", "reversible", "moved", "notes"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in manifest:
            w.writerow(r)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Inventory + non-destructive cleanup of outputs/backtests")
    ap.add_argument("--backtests-root", default="outputs/backtests")
    ap.add_argument("--out", default="outputs/backtests/phase1_artifact_inventory.csv")
    ap.add_argument("--doc", default="docs/phase1_outputs_inventory.md")
    ap.add_argument("--archive-root", default="outputs/archive/phase1_superseded")
    ap.add_argument("--dry-run", action="store_true", help="inventory only, no cleanup")
    ap.add_argument("--dry-run-cleanup", action="store_true", help="print cleanup plan, move nothing")
    ap.add_argument("--apply-cleanup", action="store_true", help="actually move archive_* dirs")
    ap.add_argument("--now", default=None, help="ISO timestamp for the manifest (default: now)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.backtests_root)
    rows = scan(root)
    write_csv(rows, Path(args.out))
    write_doc(rows, Path(args.doc))
    print(f"INVENTORY_CSV {args.out}")
    print(f"INVENTORY_DOC {args.doc}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["recommended_action"]] = counts.get(r["recommended_action"], 0) + 1
    print(f"DIRS {len(rows)} " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    plan = cleanup_plan(rows, Path(args.archive_root))
    if args.dry_run_cleanup or (plan and not args.apply_cleanup):
        print(f"CLEANUP_PLAN moves={len(plan)} (dry-run; nothing moved)")
        for p in plan:
            print(f"  MOVE {p['old_path']} -> {p['new_path']} ({p['reason']})")
    if args.apply_cleanup:
        now_iso = args.now or datetime.now(tz=timezone.utc).isoformat()
        manifest = apply_cleanup(plan, Path(args.archive_root), now_iso=now_iso)
        man_path = Path(args.archive_root) / "archive_manifest.csv"
        write_manifest(manifest, man_path)
        print(f"CLEANUP_APPLIED moved={sum(1 for m in manifest if m['moved'] == 'yes')} "
              f"manifest={man_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
