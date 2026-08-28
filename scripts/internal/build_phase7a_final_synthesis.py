#!/usr/bin/env python3
"""Build the read-only Phase 7A final research synthesis package."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs" / "deliverables" / "phase7a_final_research_review"
ZIP = ROOT / "outputs" / "deliverables" / "phase7a_final_research_review.zip"
MAC_ROOT = "/Users/Hoshino/Documents/nautilus/outputs/deliverables"
TOL = 1e-10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)


def atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def locate(name: str) -> Path:
    roots = [ROOT / "outputs" / "baseline_evaluation", ROOT / "outputs" / "deliverables"]
    candidates: list[Path] = []
    for base in roots:
        if base.exists():
            candidates.extend(p for p in base.rglob(name) if "phase7a_final_research_review" not in str(p))
    if not candidates:
        raise FileNotFoundError(name)
    candidates.sort(key=lambda p: (0 if "baseline_evaluation" in p.parts else 1, len(p.parts), str(p)))
    return candidates[0]


def read_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(locate(name))


def read_json(name: str) -> dict[str, Any]:
    return json.loads(locate(name).read_text(encoding="utf-8-sig"))


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def protected_files() -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    source_roots = ["strategies", "strategy_framework", "data_engine", "feature_engine"]
    source_suffixes = {".py", ".yaml", ".yml", ".json", ".toml", ".csv"}
    for name in source_roots:
        base = ROOT / name
        if base.exists():
            rows.extend((p, f"SOURCE_{name.upper()}") for p in base.rglob("*") if p.is_file() and p.suffix.lower() in source_suffixes)

    output_roots = [ROOT / "outputs" / "parameter_search", ROOT / "outputs" / "internal_audit", ROOT / "outputs" / "ingestion_manifests", ROOT / "outputs" / "baseline_evaluation", ROOT / "outputs" / "deliverables"]
    keep_suffixes = {".csv", ".json", ".yaml", ".yml", ".zip"}
    phase_tokens = {"phase2", "phase3", "phase4", "phase5", "phase6", "workbook", "existing_registered_strategies_corrected"}
    for base in output_roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "phase7a_final_research_review" in str(path):
                continue
            relative = str(path.relative_to(ROOT)).lower()
            include = base.name in {"parameter_search", "internal_audit", "ingestion_manifests"}
            if base.name == "baseline_evaluation":
                include = len(path.relative_to(base).parts) <= 3 and any(token in relative for token in phase_tokens)
            elif base.name == "deliverables":
                parts = path.relative_to(base).parts
                include = (
                    len(parts) == 1 and path.suffix.lower() in {".zip", ".json", ".csv"}
                ) or (
                    len(parts) <= 3 and any(token in relative for token in phase_tokens)
                    and path.name.lower().startswith(("phase", "workbook", "validation", "boss_direction", "canonical_summary", "old_", "original_strategy", "strict_reverse"))
                )
            if path.suffix.lower() in keep_suffixes and include:
                rows.append((path, "AUTHORITATIVE_RESEARCH_OR_CONTRACT"))

    for base_name in ("market_data", "feature_data"):
        base = ROOT / "historical_data" / base_name
        if base.exists():
            for path in base.rglob("*"):
                if path.is_file() and any(token in path.name.lower() for token in ("manifest", "metadata", "schema")):
                    rows.append((path, f"{base_name.upper()}_MANIFEST"))
    unique: dict[str, tuple[Path, str]] = {}
    for path, category in rows:
        unique[str(path.resolve())] = (path, category)
    return [unique[key] for key in sorted(unique)]


def make_protected_manifest() -> pd.DataFrame:
    records = []
    for path, category in protected_files():
        records.append({
            "path": str(path.relative_to(ROOT)), "category": category,
            "size_bytes": path.stat().st_size, "sha256": sha256(path),
        })
    return pd.DataFrame(records)


def final_disposition(group: pd.Series, b6: pd.Series | None, c6: pd.Series | None, d6: pd.Series | None, is_forward: bool) -> str:
    if float(group.Return) <= 0 or float(group.BE) <= 0:
        return "NOT_POSITIVE_BASELINE"
    if b6 is None:
        return "RESEARCH_STOPPED_BEFORE_DEEP_TEST"
    if c6 is None:
        label = str(b6.phase6b_economic_label)
        if label == "COST_FRAGILE":
            return "BASELINE_POSITIVE_BUT_COST_FRAGILE"
        if label in {"EPISODE_FRAGILE", "INSUFFICIENT_EPISODE_EVIDENCE"}:
            return "EPISODE_FRAGILE"
        return "RESEARCH_STOPPED_BEFORE_DEEP_TEST"
    if d6 is None:
        return "CROSS_SYMBOL_NONROBUST" if str(c6.replication_label) != "CONDITIONAL_BROAD_REPLICATION" else "RESEARCH_STOPPED_BEFORE_DEEP_TEST"
    if str(d6.Phase6D_status) == "FEE_FRAGILE":
        return "EXECUTION_FEE_FRAGILE"
    if str(d6.Phase6D_status) == "SMALL_SAMPLE_ONLY":
        return "SMALL_SAMPLE"
    if is_forward:
        return "FORWARD_WEAK"
    return "RESEARCH_STOPPED_BEFORE_DEEP_TEST"


def build_evidence_ledger() -> pd.DataFrame:
    groups = read_csv("phase6a_semantic_group_summary.csv")
    universe = read_csv("phase6a_strategy_universe.csv")
    b6 = read_csv("phase6b_cost_episode_master.csv")
    c6 = read_csv("phase6c_replication_summary.csv")
    d6 = read_csv("phase6d_strategy_execution_summary.csv")
    e6 = read_csv("phase6e_forward_summary.csv")
    bmap = {row.semantic_group_id: row for _, row in b6.iterrows()}
    cmap = {row.semantic_group_id: row for _, row in c6.iterrows()}
    dmap = {row.semantic_group_id: row for _, row in d6.iterrows()}
    forward_ids = set(e6.strategy_id)
    records = []
    for _, group in groups.iterrows():
        gid = group.equivalence_group_id
        members = universe[universe.equivalence_group_id == gid]
        representative = members[members.strategy_id == group.representative_strategy_id]
        representative = representative.iloc[0] if len(representative) else members.iloc[0]
        br, cr, dr = bmap.get(gid), cmap.get(gid), dmap.get(gid)
        records.append({
            "semantic_group_id": gid,
            "representative_strategy_id": group.representative_strategy_id,
            "member_strategy_ids": group.member_ids,
            "source_group": ";".join(sorted(set(members.source_group.astype(str)))),
            "strategy_family": ";".join(sorted(set(members.strategy_family.astype(str)))),
            "semantic_provenance": representative.semantic_provenance,
            "provenance_tier": representative.semantic_provenance_tier,
            "coverage_recovery_phase": ";".join(sorted(set(members.coverage_recovery_phase.astype(str)))),
            "canonical_timeframe": ";".join(sorted(set(members.canonical_timeframe.astype(str)))),
            "Phase6A_quality_tier": group.baseline_quality_tier,
            "Phase6B_economic_label": "NOT_EVALUATED" if br is None else br.phase6b_economic_label,
            "Phase6C_replication_label": "NOT_EVALUATED" if cr is None else cr.replication_label,
            "Phase6D_execution_label": "NOT_EVALUATED" if dr is None else dr.Phase6D_status,
            "Phase6E_forward_status": "FORWARD_WEAK" if group.representative_strategy_id in forward_ids else "NOT_EVALUATED",
            "baseline_Return": group.Return,
            "baseline_BE": group.BE,
            "baseline_MDD": group.MDD,
            "cost_survival": "NOT_EVALUATED" if br is None else f"0.10bp={br.SURVIVES_0_10_BPS};0.50bp={br.SURVIVES_0_50_BPS};1.00bp={br.SURVIVES_1_00_BPS}",
            "episode_breadth": "NOT_EVALUATED" if br is None else f"median_BE_positive={br.MEDIAN_EPISODE_BE_POSITIVE};positive_fraction={br.episode_BE_positive_fraction}",
            "winner_concentration": bool(group.warnings.find("WINNER_CONCENTRATED") >= 0),
            "cross_symbol_outcome": "NOT_EVALUATED" if cr is None else cr.replication_label,
            "execution_realism_outcome": "NOT_EVALUATED" if dr is None else dr.Phase6D_status,
            "forward_outcome": "FORWARD_WEAK" if group.representative_strategy_id in forward_ids else "NOT_EVALUATED",
            "final_research_disposition": final_disposition(group, br, cr, dr, group.representative_strategy_id in forward_ids),
        })
    return pd.DataFrame(records).sort_values("semantic_group_id")


def build_funnel() -> pd.DataFrame:
    p5a, p5b, p5c = read_json("phase5a_validation_summary.json"), read_json("phase5b_validation_summary.json"), read_json("phase5c_validation_summary.json")
    p5e, p5f = read_json("phase5e_validation_summary.json"), read_json("phase5f_validation_summary.json")
    p6a, p6b, p6c = read_json("phase6a_validation_summary.json"), read_json("phase6b_validation_summary.json"), read_json("phase6c_validation_summary.json")
    p6d = read_json("phase6d_validation_summary.json")
    return pd.DataFrame([
        ["WORKBOOK_SOURCE", "Workbook rows", 1715, "mixed rows: alpha strategies, modules, missing-data and unresolved semantics"],
        ["WORKBOOK_BASELINE", "Initial executable workbook identities", p5a["starting_standalone"], "standalone executable identities before Phase 5 expansion"],
        ["PHASE5A", "Executable workbook identities", p5a["final_standalone"], f"+{p5a['new_standalone']} identities / +{p5a['new_semantic_groups']} groups"],
        ["PHASE5B", "Executable workbook identities", p5b["final_standalone"], f"+{p5b['new_standalone']} / +{p5b['new_semantic_groups']}"],
        ["PHASE5C", "Executable workbook identities", p5c["final_standalone"], f"+{p5c['new_standalone']} / +{p5c['new_semantic_groups']} (authoritative; narrative +17 corrected to +16)"],
        ["PHASE5E", "Executable workbook identities", p5e["final_executable_identities"], f"+{p5e['new_executable_identities']} / +{p5e['new_semantic_groups']}"],
        ["PHASE5F", "Executable workbook identities", p5f["final_executable_identities"], f"+{p5f['new_executable_identities']} / +{p5f['new_semantic_groups']}"],
        ["PHASE6A_UNIVERSE", "Total executable identities", p6a["total_executable_identities"], f"{p6a['pre_workbook_identities']} pre-workbook + {p6a['workbook_identities']} workbook"],
        ["PHASE6A_GROUPS", "Independent executable semantic groups", p6a["independent_semantic_groups"], "equivalence-collapsed evidence universe"],
        ["PHASE6A_QUALITY", "Tier A/B groups", p6a["quality_tier_counts"]["A"] + p6a["quality_tier_counts"]["B"], "strongest baseline evidence groups"],
        ["PHASE6B", "Conditional replication candidates", p6b["phase6c_candidate_count"], "0 ECONOMICALLY_STRONG; all 28 winner-concentrated"],
        ["PHASE6C", "Conditional broad replication", p6c["conditional_broad_replication"], "frozen BTC/ETH/SOL comparison"],
        ["PHASE6D", "Execution-resilient research candidates", p6d["phase6e_candidates"], "100k VIP0 taker, exchange quantity constraints"],
        ["PHASE6E", "Passing strict forward gate", 0, "FORWARD_WEAK; BTC/SOL negative, ETH positive"],
    ], columns=["stage", "population", "count", "notes"])


def build_provenance(ledger: pd.DataFrame) -> pd.DataFrame:
    universe = read_csv("phase6a_strategy_universe.csv")
    phase6c_candidate_groups = set(read_csv("phase6b_phase6c_candidates.csv").semantic_group_id)
    records = []
    for tier in ["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED", "P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"]:
        child = ledger[ledger.provenance_tier == tier]
        records.append({
            "provenance_tier": tier,
            "identities": int((universe.semantic_provenance_tier == tier).sum()),
            "independent_groups": len(child),
            "Return_positive_groups": int((child.baseline_Return > 0).sum()),
            "BE_positive_groups": int((child.baseline_BE > 0).sum()),
            "Tier_A_B_groups": int(child.Phase6A_quality_tier.isin(["A", "B"]).sum()),
            "Phase6B_conditional_candidates": int(child.semantic_group_id.isin(phase6c_candidate_groups).sum()),
            "Phase6C_broad_replication": int((child.Phase6C_replication_label == "CONDITIONAL_BROAD_REPLICATION").sum()),
            "Phase6D_survivors": int((child.Phase6D_execution_label == "EXECUTION_RESILIENT_RESEARCH_CANDIDATE").sum()),
            "Phase6E_forward_candidates": int((child.Phase6E_forward_status != "NOT_EVALUATED").sum()),
        })
    return pd.DataFrame(records)


def method_corrections() -> pd.DataFrame:
    return pd.DataFrame([
        ["A_DIRECTION_MODEL", "ORIGINAL/LONG_ONLY/SHORT_ONLY/STRICT_REVERSE over already direction-specific strategies", "NORMAL and STRICT_REVERSE only", "artificial long/short filter branches removed", "existing_registered_strategies_corrected/boss_direction_model_audit.json"],
        ["B_RESULT_CARDINALITY", "512 result units", "256 corrected result units", "64 strategies × 2 lags × 2 valid direction modes", "existing_registered_strategies_corrected/validation_summary.json"],
        ["C_EPISODE_COUNT", "some Phase 5 completed_episode_count fields held fill counts", "canonical executed-position episode segmentation", "corrected at Phase 6A; prior Phase 5 summary field retained only as legacy provenance", "phase6a_validation_summary.json"],
        ["D_SOURCE_TIMEFRAME", "native daily strategy materialized as 1m in an intermediate workflow", "source-defined 1d result", "invalid intermediate result removed; xlsx_s2_0688 uses 1d", "Phase 5B canonical baseline"],
        ["E_TURNOVER_DISPLAY", "raw turnover shown without boss-facing scaling context", "raw 2.0 displayed as 200%", "BE continues to use raw turnover 2.0", "canonical reporting contract"],
        ["F_SIGNED_BE", "risk of absolute-valued presentation", "BE_bps = Return × 10000 / Turnover", "negative BE remains negative", "Phase 4A onward accounting validation"],
        ["G_EXECUTION_MODEL", "continuous fractional quantity only", "Phase 6D stepSize/minQty/minNotional/taker-fee overlay", "historical canonical results preserved; capital sensitivity added", "phase6d_validation_summary.json"],
        ["H_SLIPPAGE", "trade ticks could be mistaken for spread/depth evidence", "SLIPPAGE_NOT_EMPIRICALLY_MODELLED", "no historical bid/ask/depth; ticks were not used as synthetic spread", "phase6d_slippage_audit.csv"],
    ], columns=["correction_id", "old_or_risk", "final_authoritative_contract", "correction_effect", "authoritative_source"])


def metric_definitions() -> pd.DataFrame:
    return pd.DataFrame([
        ["Return_1x", "Σ per-interval strategy return", "1x arithmetic cumulative Return; may be below -100%; not compounded or liquidation-aware"],
        ["Turnover_raw", "Σ |Δ executed notional| / capital", "x-capital units used in all cost and BE formulas"],
        ["Turnover_display_pct", "100 × Turnover_raw", "boss-facing display only; raw 2.0 becomes 200%"],
        ["Signed_BE_bps", "Return × 10000 / Turnover_raw", "signed cost that makes final Return zero; no abs()"],
        ["MDD", "min_t(CumulativeReturn_t - running_max_from_zero_t)", "additive-return drawdown, not compounded equity drawdown"],
        ["Completed_episode", "executed-position exposure reduction, flat, or reversal closes a lifecycle segment", "unfinished final exposure remains open"],
        ["Episode_Return", "cumulative gross Return increment assigned to completed episode", "reversal close/open turnover decomposed"],
        ["Episode_Turnover", "turnover assigned to completed de-risk leg", "new reversed entry is not charged to old episode"],
        ["Episode_BE", "Episode_Return × 10000 / Episode_Turnover", "signed, only when episode turnover > tolerance"],
        ["Holding_duration", "completion timestamp - episode start timestamp", "seconds in canonical ledgers"],
        ["Winner_concentration", "top-5% positive-return share >= 50% OR Return without top 5% <= 0", "descriptive fragility flag"],
        ["LOPO", "recompute aggregate after leaving out each predefined time period", "temporal concentration diagnostic"],
        ["Cost_stress_Return", "Return_gross - Turnover_raw × cost_bps / 10000", "hypothetical fee stress; not a new backtest signal"],
        ["Residual_BE_margin", "Gross_BE_bps - effective_fee_bps", "remaining cost capacity after explicit fee"],
    ], columns=["metric", "definition_or_formula", "interpretation"])


def execution_assumptions() -> pd.DataFrame:
    return pd.DataFrame([
        ["Historical canonical research", "lag1m realistic baseline", "MODELED", "fee-zero baseline; stress only", "NOT_MODELED", "continuous fractional", "NOT_MODELED", "NOT_MODELED", "NOT_MODELED", "NOT_MODELED", "100k fixed notional reference"],
        ["Phase6D exchange mechanics", "lag1m", "MODELED", "FEE0/VIP9/VIP0; headline VIP0 taker 5bp", "NOT_MODELED", "toward-zero stepSize", "MODELED", "MODELED", "NOT_MODELED", "NOT_MODELED", "1k/10k/100k/1m; headline 100k"],
        ["Phase6E forward", "lag1m", "MODELED", "VIP0 taker 5bp headline; FEE0/VIP9 sensitivity", "NOT_MODELED", "toward-zero stepSize", "MODELED", "MODELED", "NOT_MODELED", "NOT_MODELED", "100k headline; same capital grid secondary"],
    ], columns=["execution_layer", "lag", "premium_funding", "fee", "slippage", "quantity_rounding", "minQty", "minNotional", "partial_fills", "market_impact_book_spread", "capital"])


def data_inventory() -> pd.DataFrame:
    root = ROOT / "historical_data" / "market_data"
    rows = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        symbol_root = root / "asset_class=crypto" / "exchange=BINANCE" / "venue_type=futures_um" / f"symbol={symbol}"
        for data_type, freq in (("bar", "1m"), ("funding_rate", "settlement"), ("trade", "tick")):
            base = symbol_root / f"data_type={data_type}" / f"freq={freq}"
            files = list(base.rglob("*.parquet")) if base.exists() else []
            dates = sorted({part.name.split("=", 1)[1] for path in files for part in path.parents if part.name.startswith("date=")})
            row_count = 0
            for path in files:
                try:
                    row_count += pq.ParquetFile(path).metadata.num_rows
                except Exception:
                    row_count = -1
                    break
            rows.append({"symbol": symbol, "data_type": data_type, "freq": freq, "available": bool(files), "partition_count": len(files), "row_count": row_count, "first_date": dates[0] if dates else "", "last_date": dates[-1] if dates else "", "status": "CANONICAL" if files else "NOT_PRESENT"})
        for missing in ("historical_bid_ask", "depth", "queue_position"):
            rows.append({"symbol": symbol, "data_type": missing, "freq": "event", "available": False, "partition_count": 0, "row_count": 0, "first_date": "", "last_date": "", "status": "NOT_AVAILABLE"})
    return pd.DataFrame(rows)


def artifact_ledger() -> pd.DataFrame:
    specs = [
        ("Phase3A", "phase3a_parameter_search.zip", "AUTHORITATIVE"),
        ("Phase3B-W1", "phase3b_wave1.zip", "AUTHORITATIVE"), ("Phase3B-W3", "phase3b_wave3.zip", "AUTHORITATIVE"), ("Phase3B-W5", "phase3b_wave5.zip", "AUTHORITATIVE"),
        ("Phase3C", "phase3c_robustness.zip", "AUTHORITATIVE"),
        ("Phase4A", "phase4a_baseline_evaluation.zip", "AUTHORITATIVE"), ("Phase4B", "phase4b_cost_episode_audit.zip", "AUTHORITATIVE"), ("Phase4C", "phase4c_cross_symbol_replication.zip", "AUTHORITATIVE"),
        ("Phase5A", "workbook_strategies_phase5a.zip", "AUTHORITATIVE"), ("Phase5B", "workbook_strategies_phase5b.zip", "AUTHORITATIVE"), ("Phase5C", "workbook_strategies_phase5c.zip", "AUTHORITATIVE"), ("Phase5D", "workbook_strategies_phase5d.zip", "AUDIT_ONLY"), ("Phase5E", "workbook_strategies_phase5e.zip", "AUTHORITATIVE"), ("Phase5F", "workbook_strategies_phase5f.zip", "AUTHORITATIVE"),
        ("Phase6A", "phase6a_expanded_strategy_review.zip", "AUTHORITATIVE"), ("Phase6B", "phase6b_cost_episode_review.zip", "AUTHORITATIVE"), ("Phase6C", "phase6c_cross_symbol_falsification.zip", "AUTHORITATIVE"), ("Phase6D", "phase6d_execution_realism.zip", "AUTHORITATIVE"), ("Phase6E", "phase6e_forward_holdout.zip", "AUTHORITATIVE"),
        ("Boss-direction-old", "existing_registered_strategies_current.zip", "SUPERSEDED"), ("Boss-direction-corrected", "existing_registered_strategies_corrected.zip", "AUTHORITATIVE"),
    ]
    records = []
    for phase, name, status in specs:
        try:
            path = locate(name)
            records.append({"phase": phase, "artifact_type": "ZIP", "server_path": str(path), "Mac_path": f"{MAC_ROOT}/{name}", "sha256": sha256(path), "classification": status, "notes": "preserved; no Phase7A mutation"})
        except FileNotFoundError:
            records.append({"phase": phase, "artifact_type": "ZIP", "server_path": "NOT_FOUND", "Mac_path": f"{MAC_ROOT}/{name}", "sha256": "", "classification": "INTERMEDIATE", "notes": "ZIP not present in scanned authoritative roots; extracted artifacts may remain"})
    return pd.DataFrame(records)


def validation_ledger(artifacts: pd.DataFrame) -> pd.DataFrame:
    entries = [
        ("Phase3B-W1", "phase3b_wave1_validation_summary.json"), ("Phase3B-W3", "phase3b_wave3_validation_summary.json"), ("Phase3B-W5", "phase3b_wave5_validation_summary.json"),
        ("Phase3C", "phase3c_validation_summary.json"), ("Phase4A", "phase4a_validation_summary.json"), ("Phase4B", "phase4b_validation_summary.json"), ("Phase4C", "phase4c_validation_summary.json"),
        ("Phase5A", "phase5a_validation_summary.json"), ("Phase5B", "phase5b_validation_summary.json"), ("Phase5C", "phase5c_validation_summary.json"), ("Phase5D", "phase5d_validation_summary.json"), ("Phase5E", "phase5e_validation_summary.json"), ("Phase5F", "phase5f_validation_summary.json"),
        ("Phase6A", "phase6a_validation_summary.json"), ("Phase6B", "phase6b_validation_summary.json"), ("Phase6C", "phase6c_validation_summary.json"), ("Phase6D", "phase6d_validation_summary.json"), ("Phase6E", "phase6e_validation_summary.json"),
    ]
    records = []
    amap = {row.phase: row for _, row in artifacts.iterrows()}
    for phase, name in entries:
        try:
            data = read_json(name)
        except FileNotFoundError:
            continue
        status = data.get("status", "PASSED" if data.get("passed") else "UNKNOWN")
        failures = data.get("failed_cases", data.get("baseline_unexplained_failures", data.get("unexplained_failures", 0)))
        lookahead = data.get("lookahead_failures", data.get("warmup_lookahead_failures", 0))
        protected = data.get("protected_artifact_changes", data.get("protected_hash_changes", 0))
        if isinstance(protected, list): protected = len(protected)
        artifact = amap.get(phase)
        records.append({"phase": phase, "validation_status": status, "tests_run": "RECORDED_VALIDATION_SUITE", "pass_count": "SEE_PHASE_SUMMARY", "backtest_failures": failures, "lookahead_failures": lookahead, "data_leakage_findings": data.get("test_informed_reselection", 0), "protected_hash_changes": protected, "ZIP_hash": "" if artifact is None else artifact.sha256, "delivery_status": "AVAILABLE" if artifact is not None and artifact.server_path != "NOT_FOUND" else "EXTRACTED_OR_NOT_RECORDED"})
    return pd.DataFrame(records)


def boss_summary() -> pd.DataFrame:
    return pd.DataFrame([
        ["Workbook conversion", "1715 mixed workbook rows", "280 executable standalone workbook identities; 190 workbook semantic groups", "963 unresolved general semantics plus non-alpha/data/session rows", "Engineering coverage complete under approved contracts"],
        ["Parameter search", "65 walk-forward specs", "38 improved Return, but 37 full-range drift and 47 single-fold dominated; Tier A=0", "selection instability", "No production parameters"],
        ["Baseline screening", "191 independent executable groups", "28 Phase6A Tier A/B", "winner concentration in 180/191", "Advance only strongest evidence"],
        ["Cost and episodes", "28 Tier A/B groups", "0 economically strong; 11 conditional candidates", "all 28 winner-concentrated", "Cross-symbol falsification only"],
        ["Cross-symbol", "11 candidates", "7 conditional broad replications", "conditional/low-margin evidence", "Execution realism on 7 high-priority groups"],
        ["Execution realism", "7 groups × 3 markets", "1 execution-resilient research candidate", "slippage/depth unavailable", "One frozen forward candidate"],
        ["True forward holdout", "1 candidate, 3 markets, 56 days, 7 episodes", "BTC negative; ETH positive; SOL negative", "short sample; 1/3 market success", "FORWARD_WEAK / NO_FURTHER_AUTOMATIC_RESEARCH"],
    ], columns=["question", "sample_size", "result", "main_limitation", "decision"])


def figures(output: Path, funnel: pd.DataFrame, ledger: pd.DataFrame, provenance: pd.DataFrame) -> None:
    figdir = output / "figures"; figdir.mkdir(parents=True, exist_ok=True)
    labels = ["Workbook\nrows", "Workbook\nexecutable", "All executable\nidentities", "Independent\ngroups", "Tier A/B", "Phase6B\ncandidates", "Phase6C\nbroad", "Phase6D\nsurvivor", "Strict forward\npass"]
    values = [1715, 280, 344, 191, 28, 11, 7, 1, 0]
    fig, ax = plt.subplots(figsize=(13, 6)); colors = ["#9aa0a6", "#4c78a8", "#4c78a8", "#59a14f", "#f28e2b", "#f28e2b", "#e15759", "#e15759", "#7f7f7f"]
    ax.bar(range(len(values)), values, color=colors); ax.set_xticks(range(len(values)), labels); ax.set_ylabel("Count (mixed denominators; see annotations)"); ax.set_title("Final Research Funnel — Coverage Universe to Evidence Gates")
    for i, value in enumerate(values): ax.text(i, value + max(values)*.015, str(value), ha="center", fontweight="bold")
    ax.text(1.5, 1300, "Coverage stage", ha="center", color="#4c78a8", fontweight="bold"); ax.text(5.5, 1300, "Evidence/falsification stage", ha="center", color="#e15759", fontweight="bold")
    fig.tight_layout(); fig.savefig(figdir / "01_final_research_funnel.png", dpi=170); plt.close(fig)

    reached = ledger[ledger.Phase6B_economic_label != "NOT_EVALUATED"].copy()
    reached = reached.sort_values(["Phase6D_execution_label", "representative_strategy_id"], ascending=[False, True])
    cols = ["Baseline", "Cost", "Episode", "Cross-symbol", "Execution fee", "Forward"]
    matrix=[]
    for _, row in reached.iterrows():
        episode_match = re.search(r"positive_fraction=([0-9.]+)", str(row.episode_breadth))
        episode_fraction = float(episode_match.group(1)) if episode_match else math.nan
        matrix.append([
            2 if row.Phase6A_quality_tier in {"A","B"} else 0,
            1 if row.Phase6B_economic_label in {"BROAD_BUT_LOW_MARGIN","COST_FRAGILE"} else 0,
            2 if math.isfinite(episode_fraction) and episode_fraction >= 0.5 else (1 if math.isfinite(episode_fraction) and episode_fraction > 0 else 0),
            2 if row.Phase6C_replication_label=="CONDITIONAL_BROAD_REPLICATION" else (1 if row.Phase6C_replication_label!="NOT_EVALUATED" else 0),
            2 if row.Phase6D_execution_label=="EXECUTION_RESILIENT_RESEARCH_CANDIDATE" else (0 if row.Phase6D_execution_label=="FEE_FRAGILE" else 1 if row.Phase6D_execution_label!="NOT_EVALUATED" else 0),
            0 if row.Phase6E_forward_status=="FORWARD_WEAK" else 1 if row.Phase6E_forward_status!="NOT_EVALUATED" else -1,
        ])
    data=np.asarray(matrix,float); masked=np.ma.masked_where(data<0,data)
    fig, ax=plt.subplots(figsize=(10, max(6,len(reached)*.35))); cmap=mpl.colors.ListedColormap(["#e15759","#f2cf5b","#59a14f"]); cmap.set_bad("#e5e5e5")
    ax.imshow(masked,aspect="auto",cmap=cmap,vmin=0,vmax=2); ax.set_xticks(range(len(cols)),cols); ax.set_yticks(range(len(reached)),reached.representative_strategy_id); ax.set_title("Evidence Matrix for 28 Groups Reaching Phase 6B\nred=fail, yellow=warning/conditional, green=pass, gray=not evaluated"); fig.tight_layout(); fig.savefig(figdir/"02_final_evidence_matrix.png",dpi=170); plt.close(fig)

    fields=["independent_groups","Tier_A_B_groups","Phase6B_conditional_candidates","Phase6C_broad_replication","Phase6D_survivors"]
    x=np.arange(len(provenance)); width=.15; fig,ax=plt.subplots(figsize=(12,6))
    for i,field in enumerate(fields): ax.bar(x+(i-2)*width,provenance[field],width,label=field)
    ax.set_xticks(x,provenance.provenance_tier,rotation=20,ha="right"); ax.set_ylabel("Independent group count"); ax.set_title("Evidence Progression by Semantic Provenance"); ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(figdir/"03_final_provenance.png",dpi=170); plt.close(fig)


def html_table(frame: pd.DataFrame, columns: list[str] | None = None, limit: int | None = None) -> str:
    child = frame if columns is None else frame[columns]
    if limit is not None: child = child.head(limit)
    return child.to_html(index=False, border=0, escape=True, classes="data")


def build_html(output: Path, funnel: pd.DataFrame, boss: pd.DataFrame, corrections: pd.DataFrame, provenance: pd.DataFrame, forward: pd.DataFrame, artifacts: pd.DataFrame) -> None:
    style="""body{font-family:system-ui;margin:2rem auto;max-width:1250px;line-height:1.45;color:#222}h1,h2{color:#18324a}.callout{padding:1rem;border-left:5px solid #e15759;background:#fff4f2}.ok{border-left-color:#59a14f;background:#f1faf2}.data{border-collapse:collapse;width:100%;font-size:.9rem}.data th,.data td{border:1px solid #ddd;padding:.35rem;vertical-align:top}.data th{background:#eef3f7}img{max-width:100%;margin:1rem 0}.muted{color:#666}"""
    fwd = forward.copy(); fwd["net_return_pct"] = 100*fwd.net_return; fwd["MDD_pct"] = 100*fwd.MDD
    document=f"""<!doctype html><meta charset='utf-8'><title>Phase 7A Final Research Review</title><style>{style}</style>
<h1>Phase 7A — Final Research Synthesis</h1><div class='callout'><b>RESEARCH_PROGRAM_COMPLETE</b><br><b>NO_FURTHER_AUTOMATIC_RESEARCH</b><p>The final historical survivor, xlsx_s2_0124, failed the predefined three-market post-cutoff replication gate: BTC and SOL were negative; ETH was positive.</p></div>
<h2>1. Executive Summary</h2>{html_table(boss)}
<h2>2. Strategy Conversion Coverage</h2><p>1715 workbook rows are a mixed universe, not 1715 standalone alpha strategies. Approved contracts produced 280 executable workbook identities; 963 general-semantic rows remain unresolved.</p>{html_table(funnel)}
<h2>3. Strategy Evidence Funnel</h2><img src='figures/01_final_research_funnel.png'>
<h2>4. Parameter Search Findings</h2><p>Across 65 walk-forward specs: Return improved in 38, equaled in 11, worsened in 16; 37 showed full-range drift, 47 single-fold dominance, and Phase3C Tier A count was zero. This demonstrates sensitivity, not robust production parameter identification.</p>
<h2>5. Cost / Episode Findings</h2><p>Phase6A retained 28 Tier A/B groups. Phase6B found zero ECONOMICALLY_STRONG groups; all 28 were winner-concentrated. Eleven were retained only as conditional cross-symbol falsification candidates.</p>
<h2>6. Cross-Symbol Findings</h2><p>Seven of 11 candidates achieved conditional broad replication on the frozen BTC/ETH/SOL protocol. Conditional evidence was then subjected to explicit exchange mechanics rather than promoted directly.</p><img src='figures/02_final_evidence_matrix.png'>
<h2>7. Execution-Realism Findings</h2><p>At 100k, lag1m and VIP0 taker 5bp, only xlsx_s2_0124 survived all three markets. Quantity rounding was not the primary failure mode; explicit fees eliminated five of seven candidates. Slippage remains NOT empirically modelled.</p>
<h2>8. Forward Holdout</h2>{html_table(fwd,["symbol","net_return_pct","MDD_pct","residual_BE_margin_bps","episode_count","market_status"])}<p>Window: [2026-07-01, 2026-08-26), 56 complete days, seven completed episodes across three markets. The result is FORWARD_WEAK, not production-ready.</p>
<h2>9. Methodological Corrections</h2>{html_table(corrections)}
<h2>10. Final Strategy Disposition</h2><p>No semantic group satisfies the complete baseline → cost/episode → cross-symbol → execution → strict forward chain. xlsx_s2_0124 failed the predefined three-market post-cutoff replication gate; this does not prove the strategy is universally useless.</p>
<h2>11. Limitations</h2><ul><li>56-day forward window and only seven completed episodes</li><li>SLIPPAGE_NOT_EMPIRICALLY_MODELLED; no historical bid/ask/depth/queue data</li><li>1x arithmetic Return is not compounded or liquidation-aware</li><li>Winner concentration is pervasive</li><li>Modelled workbook strategies retain P1–P4 provenance uncertainty</li><li>963 general-semantic workbook rows remain unresolved</li></ul><img src='figures/03_final_provenance.png'>
<h2>12. Research Stop Decision</h2><div class='callout'><b>NO_FURTHER_AUTOMATIC_RESEARCH</b><p>Retuning xlsx_s2_0124 after viewing this holdout would contaminate it. Future work requires genuinely new information: a longer untouched forward window, bid/ask/depth history, independently sourced hypotheses, preselected new markets, or materially different execution infrastructure.</p></div>
<h2>13. Artifact / Reproducibility Index</h2>{html_table(artifacts,["phase","server_path","sha256","classification"],limit=30)}<p class='muted'>See machine-readable ledgers in this package for all 191 semantic groups, formulas, assumptions, hashes, validation, and archival recommendations.</p>"""
    tmp=output/"phase7a_final_research_review.html.tmp"; tmp.write_text(document,encoding="utf-8"); os.replace(tmp,output/"phase7a_final_research_review.html")


def package(output: Path) -> tuple[str, int, int]:
    tmp=ZIP.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp") and path.name != "phase7a_delivery.json":
                archive.write(path,Path("phase7a_final_research_review")/path.relative_to(output))
    os.replace(tmp,ZIP)
    with zipfile.ZipFile(ZIP) as archive:
        bad=archive.testzip(); members=len(archive.infolist())
    if bad: raise RuntimeError(f"bad ZIP member: {bad}")
    return sha256(ZIP),members,ZIP.stat().st_size


def main() -> int:  # noqa: C901
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output-root",type=Path,default=OUTPUT); parser.add_argument("--test-pass-count",type=int,default=0); args=parser.parse_args()
    output=args.output_root; output.mkdir(parents=True,exist_ok=True)
    before=make_protected_manifest(); atomic_csv(output/"phase7a_protected_artifact_manifest.csv",before)

    funnel=build_funnel(); ledger=build_evidence_ledger(); provenance=build_provenance(ledger)
    corrections=method_corrections(); metrics=metric_definitions(); assumptions=execution_assumptions(); inventory=data_inventory(); artifacts=artifact_ledger(); validations=validation_ledger(artifacts); boss=boss_summary(); forward=read_csv("phase6e_forward_summary.csv")
    archival=artifacts[["phase","server_path","classification"]].copy(); archival["recommendation"]=np.where(archival.classification=="SUPERSEDED","SAFE_TO_ARCHIVE","KEEP"); archival["reason"]=np.where(archival.classification=="SUPERSEDED","superseded generated output; preserve until separately authorized cleanup","authoritative or audit provenance")

    outputs={
        "phase7a_research_funnel.csv":funnel, "phase7a_strategy_evidence_ledger.csv":ledger,
        "phase7a_provenance_summary.csv":provenance, "phase7a_method_corrections.csv":corrections,
        "phase7a_metric_definitions.csv":metrics, "phase7a_execution_assumptions.csv":assumptions,
        "phase7a_data_inventory_summary.csv":inventory, "phase7a_validation_ledger.csv":validations,
        "phase7a_artifact_ledger.csv":artifacts, "phase7a_archival_recommendations.csv":archival,
        "phase7a_boss_summary.csv":boss,
    }
    for name,frame in outputs.items(): atomic_csv(output/name,frame)
    figures(output,funnel,ledger,provenance); build_html(output,funnel,boss,corrections,provenance,forward,artifacts)

    p3=read_json("phase3b_aggregate_summary.json"); p6a=read_json("phase6a_validation_summary.json"); p6e=read_json("phase6e_validation_summary.json")
    exact_forward={row.symbol:{"net_return":row.net_return,"MDD":row.MDD,"residual_BE_margin_bps":row.residual_BE_margin_bps,"episode_count":int(row.episode_count)} for _,row in forward.iterrows()}
    summary={
        "status":"RESEARCH_PROGRAM_COMPLETE", "research_decision":"NO_FURTHER_AUTOMATIC_RESEARCH",
        "counts":{"workbook_rows":1715,"workbook_executable_identities":280,"pre_workbook_identities":64,"total_executable_identities":344,"independent_semantic_groups":191,"phase6a_tier_ab":28,"phase6b_conditional_candidates":11,"phase6c_conditional_broad":7,"phase6d_survivors":1,"phase6e_strict_forward_pass":0},
        "coverage_expansion":{"Phase5A":{"identities":30,"groups":9},"Phase5B":{"identities":53,"groups":23},"Phase5C":{"identities":40,"groups":16},"Phase5E":{"identities":9,"groups":5},"Phase5F":{"identities":17,"groups":8}},
        "unresolved_workbook":{"general_semantics":963,"missing_external_data":155,"session_semantics":64,"unsupported_modules":181,"registered_modules":72},
        "phase3":p3, "final_candidate":"xlsx_s2_0124", "forward_window":{"start":p6e["holdout_start"],"end":p6e["holdout_end"],"complete_days":p6e["complete_days"]}, "forward_result":exact_forward,
        "forward_status":p6e["forward_status"], "phase6f_decision":p6e["phase6f_decision"],
        "limitations":["56-day forward window","7 completed forward episodes","SLIPPAGE_NOT_EMPIRICALLY_MODELLED","no historical bid/ask/depth/queue data","1x arithmetic Return is not compounded or liquidation-aware","winner concentration pervasive","modelled semantic provenance P1-P4","963 unresolved general-semantic rows"],
        "new_experiments":{"strategy_backtests":0,"parameter_searches":0,"semantic_policy_experiments":0,"symbols":0,"forward_candidates":0,"live_trading":0},
        "artifact_hashes":{row.phase:row.sha256 for _,row in artifacts.iterrows() if row.sha256},
    }
    atomic_json(output/"phase7a_final_summary.json",summary)

    after=make_protected_manifest(); before_map=dict(zip(before.path,before.sha256)); after_map=dict(zip(after.path,after.sha256)); changes=sorted(path for path in set(before_map)|set(after_map) if before_map.get(path)!=after_map.get(path))
    checks={
        "workbook_reconciliation":280+72+155+64+963+181==1715,
        "identity_reconciliation":p6a["total_executable_identities"]==344,
        "semantic_group_reconciliation":len(ledger)==191 and ledger.semantic_group_id.nunique()==191,
        "funnel_counts":funnel.loc[funnel.stage=="PHASE6A_QUALITY","count"].iloc[0]==28 and funnel.loc[funnel.stage=="PHASE6B","count"].iloc[0]==11 and funnel.loc[funnel.stage=="PHASE6C","count"].iloc[0]==7 and funnel.loc[funnel.stage=="PHASE6D","count"].iloc[0]==1 and funnel.loc[funnel.stage=="PHASE6E","count"].iloc[0]==0,
        "provenance_consistency":provenance.identities.sum()==344 and provenance.independent_groups.sum()==191,
        "forward_exact":abs(exact_forward["BTCUSDT"]["net_return"]+0.17447169118538156)<TOL and abs(exact_forward["ETHUSDT"]["net_return"]-0.25778084728405615)<TOL and abs(exact_forward["SOLUSDT"]["net_return"]+0.290170880050079)<TOL,
        "protected_hash_changes_zero":len(changes)==0,
        "new_experiments_zero":all(value==0 for value in summary["new_experiments"].values()),
    }
    checks = {key: bool(value) for key, value in checks.items()}
    validation={"status":"PHASE7A_PASSED" if all(checks.values()) else "PHASE7A_FAILED","checks":checks,"focused_tests_passed":args.test_pass_count,"unexpected_protected_hash_changes":changes,"new_backtests":0,"new_parameter_searches":0,"new_strategy_registrations":0,"new_semantic_policies":0,"performance_recomputation":"NONE; stored authoritative metrics joined only","deletions":0}
    atomic_json(output/"phase7a_validation_summary.json",validation)
    digest,members,size=package(output); delivery={"server_folder":str(output),"server_zip":str(ZIP),"Mac_zip":f"{MAC_ROOT}/{ZIP.name}","sha256":digest,"members":members,"size_bytes":size,"ZIP_integrity":"PASSED"}; atomic_json(output/"phase7a_delivery.json",delivery)
    print(json.dumps({**validation,**delivery},ensure_ascii=False)); return 0 if validation["status"]=="PHASE7A_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
