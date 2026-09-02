#!/usr/bin/env python3
"""Build the final boss package and perform manifest-only output cleanup.

This script never runs research computations.  It only copies existing final
artifacts, inventories generated outputs, writes an explicit delete manifest,
and (only with --execute) deletes those exact manifest entries.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path


DELIVERY_NAME = "boss_multitimeframe_final_delivery"
PROTECTED_PARTS = {
    ".git", "src", "strategies", "configs", "registry", "tests",
    "market_data", "canonical_data", "funding", "tick_execution_index",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def copy_file(src: Path, dst: Path) -> None:
    if not src.is_file() or src.stat().st_size == 0:
        raise FileNotFoundError(f"missing or empty source: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def normalized_top(src: Path, dst: Path) -> list[dict[str, str]]:
    _, rows = read_csv(src)
    mapping = {
        "representative_strategy": "representative_strategy_id",
        "equivalent_IDs": "equivalent_strategy_ids",
        "persistent_symbols": "persistent_symbol_count",
        "persistent_ReturnBE_symbols": "persistent_Return_BE_positive_symbol_count",
        "persistent_5bp_symbols": "persistent_5bp_positive_symbol_count",
        "median_turnover_pct": "median_Turnover_pct",
        "median_run_hours": "median_directional_run_hours",
        "persistence_parameter_tunable": "parameter_tunable",
        "hold_until_opposite_required": "semantic_change_required",
    }
    fields = [
        "representative_strategy_id", "equivalent_strategy_ids", "timeframe",
        "candidate_class", "persistent_symbol_count",
        "persistent_Return_BE_positive_symbol_count",
        "persistent_5bp_positive_symbol_count", "median_Return", "median_BE",
        "median_5bp_Return", "median_Turnover_pct",
        "median_directional_run_hours", "switches_per_day",
        "persistence_mechanism", "parameter_tunable", "semantic_change_required",
    ]
    out = []
    for row in rows:
        converted = {mapping.get(k, k): v for k, v in row.items()}
        out.append({k: converted.get(k, "") for k in fields})
    write_csv(dst, fields, out)
    return out


def normalized_detail(src: Path, dst: Path, top: list[dict[str, str]]) -> None:
    _, rows = read_csv(src)
    chosen = {(r["representative_strategy_id"], r["timeframe"]) for r in top}
    rows = [r for r in rows if (r["representative_strategy_id"], r["timeframe"]) in chosen]
    fields = [
        "strategy", "timeframe", "symbol", "Return", "Return_5bp", "BE", "MDD",
        "Turnover_pct", "nonflat_fraction", "long_fraction", "short_fraction",
        "flat_fraction", "median_directional_run_hours", "P90_directional_run_hours",
        "switches_per_day",
    ]
    out = []
    for r in rows:
        out.append({
            "strategy": r.get("representative_strategy_id", ""),
            "timeframe": r.get("timeframe", ""), "symbol": r.get("symbol", ""),
            "Return": r.get("Return", ""), "Return_5bp": r.get("Return_5bp", ""),
            "BE": r.get("BE", ""), "MDD": r.get("MDD", ""),
            "Turnover_pct": r.get("turnover_percent", ""),
            "nonflat_fraction": r.get("nonflat_fraction_v2", ""),
            "long_fraction": r.get("long_fraction_v2", ""),
            "short_fraction": r.get("short_fraction_v2", ""),
            "flat_fraction": r.get("flat_fraction_v2", ""),
            "median_directional_run_hours": r.get("median_directional_run_hours", ""),
            "P90_directional_run_hours": r.get("P90_directional_run_hours", ""),
            "switches_per_day": r.get("sign_switches_per_day", ""),
        })
    if len(out) != len(top) * 9:
        raise ValueError(f"top detail rows {len(out)} != {len(top) * 9}")
    write_csv(dst, fields, out)


def normalized_comparison(src: Path, dst: Path) -> None:
    fields, rows = read_csv(src)
    rename = {
        "median_turnover_pct": "median_Turnover_pct",
        "median_directional_run_hours": "directional_run_duration_hours",
        "median_switches_per_day": "switches_per_day",
    }
    out_fields = [rename.get(x, x) for x in fields]
    out = [{rename.get(k, k): v for k, v in row.items()} for row in rows]
    write_csv(dst, out_fields, out)


def normalized_parameter(src: Path, dst: Path) -> None:
    fields, rows = read_csv(src)
    useful = [r for r in rows if str(r.get("useful_existing_parameter_example", "")).lower() == "true"]
    if not useful:
        useful = rows
    write_csv(dst, fields, useful)


def build_delivery(outputs: Path) -> dict[str, object]:
    boss = outputs / "baseline_evaluation" / "boss_multitimeframe_tick_screen"
    final = boss / "boss_final_review"
    distill = boss / "final_candidate_distillation"
    follow = boss / "persistent_v2_followup"
    delivery = outputs / "deliverables" / DELIVERY_NAME
    if delivery.exists():
        shutil.rmtree(delivery)
    key = delivery / "01_key_results"
    full = delivery / "02_full_results"
    key.mkdir(parents=True)
    full.mkdir(parents=True)

    top = normalized_top(final / "boss_final_top_candidates.csv", key / "top_10m15m_candidates.csv")
    normalized_detail(final / "boss_final_candidate_symbol_detail.csv", key / "top_candidates_9symbol_detail.csv", top)
    normalized_comparison(final / "boss_final_14_timeframe_comparison.csv", key / "top_candidates_1m_5m_10m_15m_comparison.csv")
    normalized_parameter(final / "boss_final_parameter_case.csv", key / "persistence_parameter_result.csv")

    key_sources = [
        "boss_final_top_candidates.csv", "boss_final_14_strategy_groups.csv",
        "boss_final_14_timeframe_comparison.csv", "boss_final_candidate_breadth.csv",
        "boss_final_position_mechanism.csv", "boss_final_parameter_case.csv",
        "boss_final_key_answers.csv",
    ]
    for name in key_sources:
        copy_file(final / name, key / name)

    full_sources = {
        boss / "boss_multitimeframe_tick_master.csv": "boss_multitimeframe_tick_master.csv",
        boss / "boss_multitimeframe_strategy_summary.csv": "boss_multitimeframe_strategy_summary.csv",
        boss / "boss_multitimeframe_symbol_summary.csv": "boss_multitimeframe_symbol_summary.csv",
        boss / "boss_multitimeframe_timeframe_summary.csv": "boss_multitimeframe_timeframe_summary.csv",
        boss / "persistent_position_metrics_v2.csv": "persistent_position_metrics_v2.csv",
        boss / "persistent_position_candidates_v2.csv": "persistent_position_candidates_v2.csv",
        follow / "persistent_strategy_timeframe_summary.csv": "persistent_strategy_timeframe_summary.csv",
        follow / "persistent_cross_symbol_matrix.csv": "persistent_cross_symbol_matrix.csv",
        distill / "boss_10m15m_strategy_summary.csv": "boss_10m15m_strategy_summary.csv",
        distill / "boss_10m_vs_15m_comparison.csv": "boss_10m_vs_15m_comparison.csv",
        distill / "boss_10m15m_independent_candidates.csv": "boss_10m15m_independent_candidates.csv",
        distill / "boss_10m15m_final_shortlist.csv": "boss_10m15m_final_shortlist.csv",
        final / "boss_final_candidate_symbol_detail.csv": "boss_final_candidate_symbol_detail.csv",
        final / "boss_final_candidate_breadth.csv": "boss_final_candidate_breadth.csv",
        follow / "persistence_parameter_sensitivity_v2.csv": "persistence_parameter_sensitivity_v2.csv",
        follow / "persistence_structure_audit_v2.csv": "persistence_structure_audit_v2.csv",
        follow / "hold_until_opposite_feasibility_v2.csv": "hold_until_opposite_feasibility_v2.csv",
        boss / "reference_position_behavior.csv": "reference_position_behavior.csv",
        final / "boss_final_14_strategy_groups.csv": "boss_final_14_strategy_groups.csv",
        final / "boss_final_14_timeframe_comparison.csv": "boss_final_14_timeframe_comparison.csv",
        final / "boss_final_position_mechanism.csv": "boss_final_position_mechanism.csv",
        final / "boss_final_parameter_case.csv": "boss_final_parameter_case.csv",
        final / "boss_final_hold_until_opposite_review.csv": "boss_final_hold_until_opposite_review.csv",
        final / "boss_final_key_answers.csv": "boss_final_key_answers.csv",
        final / "boss_final_top_candidates.csv": "boss_final_top_candidates.csv",
    }
    for src, name in full_sources.items():
        copy_file(src, full / name)

    figure_src = final / "figures"
    figures = sorted(p for p in figure_src.rglob("*.png") if p.is_file())
    if not figures:
        raise ValueError("no final figures found")
    for src in figures:
        rel = src.relative_to(figure_src)
        copy_file(src, key / "figures" / rel)
        copy_file(src, full / "figures" / rel)

    _, master_rows = read_csv(full / "boss_multitimeframe_tick_master.csv")
    if len(master_rows) != 9612:
        raise ValueError(f"master row count {len(master_rows)} != 9612")
    _, groups = read_csv(key / "boss_final_14_strategy_groups.csv")
    _, breadth = read_csv(key / "boss_final_candidate_breadth.csv")
    if len(groups) != 14 or len(breadth) != 24:
        raise ValueError(f"candidate reconciliation failed: groups={len(groups)}, rows={len(breadth)}")

    manifest_rows = []
    for p in sorted(x for x in delivery.rglob("*") if x.is_file()):
        rel = p.relative_to(delivery).as_posix()
        category = "FIGURE" if p.suffix.lower() == ".png" else ("KEY_RESULT" if rel.startswith("01_key_results/") else "FULL_RESULT")
        manifest_rows.append({"relative_path": rel, "size_bytes": p.stat().st_size, "sha256": sha256(p), "category": category})
    write_csv(full / "final_delivery_manifest.csv", ["relative_path", "size_bytes", "sha256", "category"], manifest_rows)

    zip_path = outputs / "deliverables" / f"{DELIVERY_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(x for x in delivery.rglob("*") if x.is_file()):
            zf.write(p, (Path(DELIVERY_NAME) / p.relative_to(delivery)).as_posix())
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise ValueError(f"ZIP CRC failed: {bad}")
    return {
        "delivery": str(delivery), "zip": str(zip_path), "zip_sha256": sha256(zip_path),
        "key_file_count": sum(1 for p in key.rglob("*") if p.is_file()),
        "full_file_count": sum(1 for p in full.rglob("*") if p.is_file()),
        "delivery_file_count": sum(1 for p in delivery.rglob("*") if p.is_file()),
        "delivery_bytes": sum(p.stat().st_size for p in delivery.rglob("*") if p.is_file()),
        "zip_bytes": zip_path.stat().st_size, "master_rows": len(master_rows),
        "candidate_groups": len(groups), "candidate_timeframe_rows": len(breadth),
        "figure_count": len(figures), "validation": "PASSED",
    }


def classify(rel: Path) -> tuple[str, str, str]:
    parts = rel.parts
    low = [p.lower() for p in parts]
    if len(parts) >= 2 and low[0] == "deliverables":
        name = low[1]
        current_prefixes = (
            "boss_multitimeframe_final_delivery",
            "all_converted_workbook_strategies",
            "existing_registered_strategies_corrected",
            "phase7a_final_research_review",
            "current_strategy_results",
            "vwm_binance_um_2y_1m_vol_targeted",
            "phase1_vwm_crypto_perpetual_2026q2",
        )
        if name.startswith(current_prefixes):
            return "KEEP_FINAL_DELIVERY", "current or separate final deliverable", str(rel)
        obsolete_prefixes = (
            "phase", "workbook_strategies_phase", "workbook_strategies_baseline",
            "workbook_modules_phase", "existing_registered_strategies_current",
            "generalized_nm_trade_be_current", "boss_delivery_", "strict_",
            "fixed_", "multiclock_", "ma_crossover_", "boss_corrected_smoke",
            "existing_registered_strategies_smoke", "constant_notional_",
            "direction_compare", "episode_diagnostics", "forward_turnover",
            "reverse_turnover", "risk_leverage", "lag_sweep",
        )
        if name.startswith(obsolete_prefixes) or name.endswith((".stdout.log", ".stderr.log", ".pid", ".launch.json")):
            return "DELETE_SUPERSEDED", "older stage delivery superseded by preserved current final deliverables", f"deliverables/{DELIVERY_NAME}"
        return "KEEP_UNCERTAIN", "separate deliverable not proven superseded", ""
    if "tick_execution_index" in low:
        return "KEEP_CANONICAL_INTERNAL", "compact exact tick execution index", str(rel)
    if low and low[0] == "baseline_evaluation" and "boss_multitimeframe_tick_screen" in low:
        name = low[-1]
        if name in {"tick_execution_index_manifest.csv", "tick_execution_index_spot_validation.csv", "boss_tick_index_data_window.json", "official_raw_trade_daily_availability.csv", "official_raw_trade_archive_samples.csv"}:
            return "KEEP_CANONICAL_INTERNAL", "tick index provenance", str(rel)
        return "DELETE_SUPERSEDED", "copied into validated final boss delivery or obsolete experiment rendering", f"deliverables/{DELIVERY_NAME}"
    if low and low[0] in {"backtests", "batches", "visual_qa", "architecture_inventory", "cleanup_inventory", "parameter_search"}:
        if any(token in low for token in PROTECTED_PARTS):
            return "KEEP_UNCERTAIN", "generated path name intersects protected-token policy", ""
        return "DELETE_SUPERSEDED", "historical generated stage/batch output superseded by final deliverables", f"deliverables/{DELIVERY_NAME}"
    if low and low[0] == "baseline_evaluation" and len(low) > 1 and low[1].startswith("phase"):
        return "DELETE_SUPERSEDED", "historical phase evaluation superseded by Phase 7A and final boss delivery", "deliverables/phase7a_final_research_review"
    if low and low[0] in {"archive", "ab_check", "logs"}:
        return "DELETE_SUPERSEDED", "superseded generated archive/check/log output", "deliverables/phase7a_final_research_review"
    if low and low[0] in {"tmp", "temp", "cache", "tmp_tick_ingest"}:
        return "DELETE_TEMPORARY", "regenerable completed temporary/cache output", ""
    if low and low[0] == "internal_audit":
        return "KEEP_CANONICAL_INTERNAL", "small internal provenance/audit asset", str(rel)
    return "KEEP_UNCERTAIN", "not proven superseded", ""


def inventory_and_manifest(outputs: Path, audit: Path, machine: str) -> dict[str, object]:
    audit.mkdir(parents=True, exist_ok=True)
    inventory_rows = []
    delete_rows = []
    total_bytes = 0
    for p in sorted(x for x in outputs.rglob("*") if x.is_file()):
        rel = p.relative_to(outputs)
        size = p.stat().st_size
        total_bytes += size
        cls, reason, replacement = classify(rel)
        digest = sha256(p) if size <= 50 * 1024 * 1024 or cls.startswith("KEEP") else "SKIPPED_LARGE"
        row = {
            "machine": machine, "path": str(p), "relative_path": rel.as_posix(),
            "size_bytes": size, "modified_time": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
            "file_type": p.suffix.lower() or "none", "apparent_workflow": rel.parts[0] if rel.parts else "",
            "apparent_phase_stage": rel.parts[1] if len(rel.parts) > 1 else "",
            "sha256_or_status": digest, "classification": cls, "reason": reason,
            "replacement_or_final_source": replacement,
        }
        inventory_rows.append(row)
        if cls in {"DELETE_SUPERSEDED", "DELETE_TEMPORARY"}:
            delete_rows.append({
                "exact_path": str(p.resolve()), "type": "file", "size_bytes": size,
                "reason": reason, "replacement_or_final_source": replacement,
                "classification": cls, "validation_status": "DRY_RUN_VALIDATED",
            })
    write_csv(audit / f"cleanup_inventory_{machine}.csv", list(inventory_rows[0]) if inventory_rows else [], inventory_rows)
    fields = ["exact_path", "type", "size_bytes", "reason", "replacement_or_final_source", "classification", "validation_status"]
    write_csv(audit / f"cleanup_delete_manifest_{machine}.csv", fields, delete_rows)
    return {
        "machine": machine, "outputs_bytes_before": total_bytes,
        "inventory_files": len(inventory_rows), "delete_files_planned": len(delete_rows),
        "reclaimable_bytes": sum(int(r["size_bytes"]) for r in delete_rows),
        "classification_counts": {c: sum(1 for r in inventory_rows if r["classification"] == c) for c in sorted({r["classification"] for r in inventory_rows})},
        "manifest": str(audit / f"cleanup_delete_manifest_{machine}.csv"),
    }


def execute_manifest(outputs: Path, manifest: Path) -> dict[str, int]:
    _, rows = read_csv(manifest)
    delivery = (outputs / "deliverables" / DELIVERY_NAME).resolve()
    deleted_files = 0
    deleted_bytes = 0
    for row in rows:
        target = Path(row["exact_path"]).resolve()
        try:
            target.relative_to(outputs.resolve())
        except ValueError as exc:
            raise ValueError(f"delete target outside outputs: {target}") from exc
        if target == delivery or delivery in target.parents:
            raise ValueError(f"final delivery protected: {target}")
        rel_parts = [p.lower() for p in target.relative_to(outputs.resolve()).parts]
        if "tick_execution_index" in rel_parts or any(p in {"market_data", "canonical_data"} for p in rel_parts):
            raise ValueError(f"protected target: {target}")
        if target.is_file():
            size = target.stat().st_size
            target.chmod(target.stat().st_mode | stat.S_IWRITE)
            target.unlink()
            deleted_files += 1
            deleted_bytes += size
    deleted_dirs = 0
    for d in sorted((p for p in outputs.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if d == outputs or d == delivery or delivery in d.parents:
            continue
        try:
            next(d.iterdir())
        except StopIteration:
            d.rmdir()
            deleted_dirs += 1
    return {"deleted_files": deleted_files, "deleted_folders": deleted_dirs, "deleted_bytes": deleted_bytes}


def validate_final(outputs: Path, require_tick_index: bool = False) -> dict[str, object]:
    delivery = outputs / "deliverables" / DELIVERY_NAME
    zip_path = outputs / "deliverables" / f"{DELIVERY_NAME}.zip"
    _, master = read_csv(delivery / "02_full_results" / "boss_multitimeframe_tick_master.csv")
    _, groups = read_csv(delivery / "01_key_results" / "boss_final_14_strategy_groups.csv")
    _, rows = read_csv(delivery / "01_key_results" / "boss_final_candidate_breadth.csv")
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        zip_files = len([x for x in zf.infolist() if not x.is_dir()])
    pngs = list(delivery.rglob("*.png"))
    bad_pngs = []
    for p in pngs:
        with p.open("rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                bad_pngs.append(str(p))
    tick_root = outputs / "baseline_evaluation" / "boss_multitimeframe_tick_screen" / "tick_execution_index"
    tick_manifest = tick_root.parent / "tick_execution_index_manifest.csv"
    if require_tick_index and (not tick_root.is_dir() or not tick_manifest.is_file()):
        raise ValueError("compact tick execution index or manifest missing")
    result = {
        "status": "PASSED", "master_rows": len(master), "candidate_groups": len(groups),
        "candidate_timeframe_rows": len(rows), "delivery_files": sum(1 for p in delivery.rglob("*") if p.is_file()),
        "delivery_bytes": sum(p.stat().st_size for p in delivery.rglob("*") if p.is_file()),
        "zip_files": zip_files, "zip_test": "PASSED" if bad is None else f"FAILED:{bad}",
        "zip_sha256": sha256(zip_path), "png_files": len(pngs), "bad_pngs": len(bad_pngs),
        "tick_index_exists": tick_root.is_dir(), "tick_manifest_exists": tick_manifest.is_file(),
        "outputs_bytes": sum(p.stat().st_size for p in outputs.rglob("*") if p.is_file()),
        "disk_free": shutil.disk_usage(outputs).free,
    }
    if len(master) != 9612 or len(groups) != 14 or len(rows) != 24 or bad is not None or bad_pngs:
        raise ValueError(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--require-tick-index", action="store_true")
    args = parser.parse_args()
    outputs = args.outputs.resolve()
    audit = (args.audit or outputs / "internal_audit" / "final_manifests").resolve()
    result: dict[str, object] = {}
    if args.build:
        result["delivery"] = build_delivery(outputs)
    if args.inventory:
        result["inventory"] = inventory_and_manifest(outputs, audit, args.machine)
    if args.execute:
        manifest = audit / f"cleanup_delete_manifest_{args.machine}.csv"
        result["cleanup"] = execute_manifest(outputs, manifest)
    if args.validate:
        result["validation"] = validate_final(outputs, args.require_tick_index)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
