#!/usr/bin/env python3
"""Assemble the two-method boss delivery and internal reverse-validation tables."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
TIMEFRAMES = ("1m", "10m", "15m")
WORK = Path("outputs/baseline_evaluation/execution_method_and_reverse_review")
DELIVERY = Path("outputs/deliverables/execution_method_and_reverse_review")
PILOT = Path("outputs/baseline_evaluation/maker_execution_research/l1_pilot")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def atomic_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def persistence(position: np.ndarray) -> tuple[int, dict]:
    signs = np.sign(np.asarray(position, dtype=float)).astype(np.int8)
    change = np.flatnonzero(np.r_[True, signs[1:] != signs[:-1]])
    ends = np.r_[change[1:], len(signs)]
    held = (ends - change)[signs[change] != 0] / 60.0
    switches = int(((signs[1:] * signs[:-1]) < 0).sum())
    days = len(signs) / 1440.0
    result = {
        "nonflat_fraction": float((signs != 0).mean()),
        "median_directional_run_hours": float(np.median(held)) if len(held) else 0.0,
        "p90_directional_run_hours": float(np.quantile(held, .9)) if len(held) else 0.0,
        "switches_per_day": switches / days if days else 0.0,
    }
    flag = int(
        result["nonflat_fraction"] >= .90
        and result["median_directional_run_hours"] >= 24
        and result["switches_per_day"] <= 1
    )
    return flag, result


def normalize_metrics(metric: pd.Series, model: str) -> dict:
    if "MAKER" not in model:
        return {
            "Return": metric.Return,
            "Sharpe": metric.Sharpe,
            "BE": metric.Signed_BE_bps,
            "MaxDD": metric.Max_Drawdown,
            "Turnover": metric.Turnover_raw,
            "quantity_fill_ratio": math.nan,
            "zero_fill_rate": math.nan,
            "mean_abs_target_position_error": 0.0,
        }
    return {
        "Return": metric.Return_gross,
        "Sharpe": metric.Sharpe_gross,
        "BE": metric.Signed_BE_bps_gross,
        "MaxDD": metric.Max_Drawdown_gross,
        "Turnover": metric.Turnover_raw,
        "quantity_fill_ratio": metric.quantity_fill_ratio,
        "zero_fill_rate": metric.zero_fill_order_rate,
        "mean_abs_target_position_error": metric.mean_absolute_target_position_error,
    }


def load_path(root: Path, symbol: str, case_key: str, model: str) -> pd.DataFrame:
    suffix = "FIRST_TICK" if model == "FIRST_TICK_IDEALIZED" else "MAKER"
    return pd.read_parquet(root / "maker_comparison" / "paths" / symbol / f"{case_key}__{suffix}.parquet")


def render_detail(
    frame: pd.DataFrame,
    destination: Path,
    strategy: str,
    symbol: str,
    timeframe: str,
    model: str,
    metric: dict,
) -> None:
    if "FIRST_TICK" in model:
        cumulative = frame.cumulative_return.to_numpy(float)
        turnover = frame.turnover.to_numpy(float)
        position = frame.position.to_numpy(float)
    else:
        cumulative = frame.cumulative_return_gross.to_numpy(float)
        turnover = frame.cumulative_turnover.to_numpy(float)
        position = frame.actual_position.to_numpy(float)
    times = pd.to_datetime(frame.timestamp_ns, unit="ns", utc=True)
    equity = 1 + cumulative
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    dd = np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1
    trough = int(np.argmin(dd))
    _, extra = persistence(position)
    fig, axes = plt.subplots(3, 1, figsize=(12.5, 8.5), sharex=True, constrained_layout=True)
    axes[0].plot(times, cumulative, color="#1665a7", lw=1.1, label="Cumulative 1x Return")
    twin = axes[0].twinx()
    twin.plot(times, turnover, color="#d47a18", lw=.9, alpha=.8, label="Cumulative Turnover")
    axes[0].set_ylabel("1x Return")
    twin.set_ylabel("Turnover (x)")
    axes[1].step(times, np.sign(position), where="post", color="#333333", lw=.7)
    axes[1].fill_between(times, 0, np.sign(position), where=np.sign(position)>0, step="post", color="#3a9d5d", alpha=.6)
    axes[1].fill_between(times, 0, np.sign(position), where=np.sign(position)<0, step="post", color="#c84e4e", alpha=.6)
    axes[1].set_yticks([-1, 0, 1], ["SHORT", "FLAT", "LONG"])
    axes[1].set_ylabel("Executed state")
    axes[1].text(
        .01, .95,
        f"Nonflat={extra['nonflat_fraction']:.1%} | Median run={extra['median_directional_run_hours']:.1f}h | "
        f"P90={extra['p90_directional_run_hours']:.1f}h | Switches/day={extra['switches_per_day']:.2f}",
        transform=axes[1].transAxes, va="top", fontsize=8,
        bbox={"facecolor":"white", "alpha":.85, "edgecolor":"none"},
    )
    axes[2].plot(times, dd, color="#b04444", lw=1)
    axes[2].scatter(times[trough], dd[trough], color="black", s=24, zorder=4)
    axes[2].annotate(
        f"MaxDD={dd[trough]:.2%}\n{times[trough].date()}",
        (times[trough], dd[trough]), xytext=(8, 10), textcoords="offset points", fontsize=8,
    )
    axes[2].set_ylabel("Drawdown")
    axes[2].set_xlabel("UTC")
    fig.suptitle(
        f"{strategy} | {symbol} | {timeframe} | {model}\n"
        f"Return={metric['Return']:.2%} | Sharpe={metric['Sharpe']:.2f} | "
        f"Signed BE={metric['BE']:.2f} bps | MaxDD={metric['MaxDD']:.2%} | Turnover={metric['Turnover']:.2f}x"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=125)
    plt.close(fig)


def render_summary(frame: pd.DataFrame, destination: Path, strategy: str, timeframe: str, model: str) -> None:
    rows = ["Return (1x, %)", "Signed BE (bps)", "Sharpe", "Max DD (%)", "Persistent"]
    values = np.full((5, len(SYMBOLS)), np.nan)
    for row in frame.itertuples(index=False):
        j = SYMBOLS.index(row.symbol)
        values[:, j] = [row.Return*100, row.BE, row.Sharpe, row.MaxDD*100, row.Persistent]
    scaled = np.zeros_like(values)
    for i in range(4):
        finite = np.abs(values[i][np.isfinite(values[i])])
        limit = finite.max(initial=1.0)
        scaled[i] = values[i] / limit
    scaled[4] = np.where(np.isnan(values[4]), np.nan, values[4]*2-1)
    masked = np.ma.masked_invalid(scaled)
    fig, ax = plt.subplots(figsize=(13, 4.6), constrained_layout=True)
    cmap = plt.get_cmap("RdYlGn").copy()
    cmap.set_bad("#eeeeee")
    ax.imshow(masked, cmap=cmap, norm=TwoSlopeNorm(vmin=-1, vcenter=0, vmax=1), aspect="auto")
    ax.set_xticks(range(len(SYMBOLS)), SYMBOLS, rotation=35, ha="right")
    ax.set_yticks(range(5), rows)
    for i in range(5):
        for j in range(len(SYMBOLS)):
            if not np.isfinite(values[i, j]):
                text = "—"
            elif i == 4:
                text = str(int(values[i, j]))
            else:
                text = f"{values[i, j]:.2f}"
            ax.text(j, i, text, ha="center", va="center", fontsize=8)
    for j in range(len(SYMBOLS)):
        if np.isfinite(values[0, j]):
            ax.add_patch(Rectangle((j-.49, -.49), .98, 4.98, fill=False, edgecolor="#111111", lw=1.8))
            ax.text(j+.38, -.32, "Q", ha="center", va="center", fontsize=7, weight="bold")
    ax.set_title(f"{strategy} | {timeframe} | {model}\nMarch-2024 paired execution window; Q = FIRST_TICK-selected case")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=125)
    plt.close(fig)


def build_provenance(repo: Path, work: Path, delivery: Path) -> None:
    source = work / "data_provenance/maker_market_data_source.csv"
    frame = pd.read_csv(source)
    checksum_valid = frame.checksum_valid.astype(str).str.lower().eq("true")
    if len(frame) != 9*30*2 or not checksum_valid.all():
        raise ValueError(f"maker provenance incomplete: {len(frame)} rows")
    (delivery / "data_provenance").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, delivery / "data_provenance/maker_market_data_source.csv")
    availability = work / "data_provenance/maker_archive_availability.csv"
    if availability.exists():
        shutil.copy2(availability, delivery / "data_provenance/maker_archive_availability.csv")
    note = """# Maker market-data source\n\nMaker passive prices use Binance official historical USD-M Futures `bookTicker` L1 best bid/ask and sizes, converted to Nautilus `QuoteTick`. Passive fill triggering uses Binance official historical USD-M Futures `trades`, converted to Nautilus `TradeTick`. Published archive checksums, chronology, non-crossed BBO, and positive sizes were validated. `bookTicker` is L1 BBO only: it has no L2 depth, true queue-ahead position, or L3 order identity. The headline model is therefore `L1_BBO_MAKER`, not queue-realistic execution.\n"""
    path = delivery / "data_provenance/data_source_note.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note, encoding="utf-8")


def aggregate_reverse(work: Path, delivery: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    shards = sorted((work / "reverse_validation/shards").glob("*/reverse_case_comparison.csv"))
    if len(shards) != 9:
        raise ValueError(f"reverse shards incomplete: {len(shards)}/9")
    cases = pd.concat([pd.read_csv(path) for path in shards], ignore_index=True)
    frozen_origin = pd.read_csv(work / "selection/first_tick_selected_cases.csv")[[
        "strategy_id", "symbol", "timeframe", "source_origin"
    ]].drop_duplicates()
    cases = cases.merge(frozen_origin, on=["strategy_id", "symbol", "timeframe"], how="left", validate="one_to_one")
    if cases.source_origin.isna().any():
        raise ValueError("reverse source-origin reconciliation failed")
    manifest_columns = [
        "strategy_id", "semantic_group_id", "source_origin", "symbol", "timeframe", "validation_type",
        "discovery_start", "discovery_end_exclusive", "validation_start", "validation_end_exclusive",
        "Return_NORMAL_DISCOVERY", "Sharpe_NORMAL_DISCOVERY", "Signed_BE_bps_NORMAL_DISCOVERY",
        "normal_discovery_Return_negative", "normal_discovery_Sharpe_negative", "normal_discovery_BE_negative",
    ]
    manifests = cases[manifest_columns].copy()
    root = delivery / "reverse_validation"
    atomic_csv(manifests, root / "reverse_candidate_manifest.csv")
    atomic_csv(cases, root / "reverse_case_comparison.csv")
    reverse_flag = cases.reverse_validation_positive.astype(str).str.lower().eq("true")
    validated = cases[reverse_flag].sort_values(
        ["Signed_BE_bps_REVERSE", "Sharpe_REVERSE", "Return_REVERSE"], ascending=False
    )
    atomic_csv(validated, root / "validated_reverse_candidates.csv")
    summary_rows = []
    for strategy_id, group in cases.groupby("strategy_id"):
        good = group[group.reverse_validation_positive.astype(str).str.lower().eq("true")]
        summary_rows.append(
            {
                "strategy_id": strategy_id,
                "semantic_group_id": group.semantic_group_id.iloc[0],
                "negative_NORMAL_cases_tested": len(group),
                "in_sample_reverse_positive": int(group.Return_REVERSE_DISCOVERY.gt(0).sum()),
                "validation_reverse_positive": len(good),
                "validation_positive_symbols": ";".join(sorted(good.symbol.unique())),
                "validation_positive_symbol_count": good.symbol.nunique(),
                "validation_positive_timeframes": ";".join(sorted(good.timeframe.unique())),
                "validation_positive_timeframe_count": good.timeframe.nunique(),
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(
        ["validation_reverse_positive", "in_sample_reverse_positive", "negative_NORMAL_cases_tested"],
        ascending=False,
    )
    atomic_csv(summary, root / "reverse_strategy_summary.csv")
    return cases, validated


def render_reverse_figures(work: Path, delivery: Path, cases: pd.DataFrame) -> None:
    for row in cases.itertuples(index=False):
        path_key = "case_" + hashlib.sha256(
            f"{row.semantic_group_id}|{row.timeframe}".encode()
        ).hexdigest()[:20]
        source_root = work / "reverse_validation/shards" / row.symbol / "paths" / path_key
        for variant in ("NORMAL", "STRICT_REVERSE"):
            metric_variant = variant if variant == "NORMAL" else "REVERSE"
            for window in ("DISCOVERY", "VALIDATION"):
                source = pd.read_parquet(source_root / f"{variant}__{window}.parquet").rename(
                    columns={
                        "event_time_ns":"timestamp_ns",
                        "cumulative_turnover":"turnover",
                        "executed_position":"position",
                    }
                )
                suffix = "_DISCOVERY" if window == "DISCOVERY" else ""
                metric = {
                    "Return": getattr(row, f"Return_{metric_variant}{suffix}"),
                    "Sharpe": getattr(row, f"Sharpe_{metric_variant}{suffix}"),
                    "BE": getattr(row, f"Signed_BE_bps_{metric_variant}{suffix}"),
                    "MaxDD": getattr(row, f"MaxDD_{metric_variant}{suffix}"),
                    "Turnover": getattr(row, f"Turnover_raw_{metric_variant}{suffix}"),
                }
                destination = (
                    delivery / "reverse_validation" / variant / "FIRST_TICK" / row.strategy_id
                    / row.timeframe / f"{row.symbol}__{window.lower()}.png"
                )
                label = "EXPLORATORY_IN_SAMPLE_REVERSE" if window == "DISCOVERY" else "INTERNAL_TEMPORAL_VALIDATION"
                render_detail(
                    source, destination, row.strategy_id, row.symbol, row.timeframe,
                    f"{variant} FIRST_TICK | {label}", metric,
                )


def render_reverse_maker_sensitivity(work: Path, delivery: Path, cases: pd.DataFrame) -> int:
    reverse_root = work / "reverse_maker_comparison"
    if not (reverse_root / "execution_metrics.csv").exists():
        return 0
    mapping = pd.read_csv(work / "maker_signals/maker_case_mapping.csv").set_index(
        ["symbol", "semantic_group_id", "timeframe"]
    )
    normal_metrics = pd.read_csv(work / "maker_comparison/execution_metrics.csv").set_index(
        ["symbol", "case_key", "execution_model"]
    )
    reverse_metrics = pd.read_csv(reverse_root / "execution_metrics.csv").set_index(
        ["symbol", "case_key", "execution_model"]
    )
    rendered = 0
    for row in cases.itertuples(index=False):
        case_key = mapping.loc[(row.symbol, row.semantic_group_id, row.timeframe), "case_key"]
        if isinstance(case_key, pd.Series):
            case_key = case_key.iloc[0]
        variants = (
            (
                "NORMAL",
                work / "maker_comparison/paths" / row.symbol / f"{case_key}__MAKER.parquet",
                normal_metrics.loc[(row.symbol, case_key, "GTC_UNTIL_SIGNAL_INVALID")],
            ),
            (
                "STRICT_REVERSE",
                reverse_root / "paths" / row.symbol / f"{case_key}__MAKER.parquet",
                reverse_metrics.loc[(row.symbol, case_key, "STRICT_REVERSE_GTC_UNTIL_SIGNAL_INVALID")],
            ),
        )
        for variant, path, raw_metric in variants:
            frame = pd.read_parquet(path)
            metric = normalize_metrics(raw_metric, "MAKER")
            destination = (
                delivery / "reverse_validation" / variant / "MAKER" / row.strategy_id
                / row.timeframe / f"{row.symbol}__historical_march_sensitivity.png"
            )
            render_detail(
                frame, destination, row.strategy_id, row.symbol, row.timeframe,
                f"{variant} L1_BBO_MAKER | HISTORICAL_SENSITIVITY_ONLY", metric,
            )
            rendered += 1
    return rendered


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--delivery", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    work = (args.work or repo / WORK).resolve()
    delivery = (args.delivery or repo / DELIVERY).resolve()
    frozen = pd.read_csv(work / "selection/first_tick_selected_cases.csv")
    freeze_hash = sha256(work / "selection/first_tick_selected_cases.csv")
    mapping = pd.read_csv(work / "maker_signals/maker_case_mapping.csv")
    metrics = pd.read_csv(work / "maker_comparison/execution_metrics.csv")
    if len(frozen) != 878:
        raise ValueError(f"frozen selection changed: {len(frozen)}")
    map_key = mapping.set_index(["symbol", "semantic_group_id", "timeframe"])["case_key"]
    metric_lookup = metrics.set_index(["symbol", "case_key", "execution_model"])
    rows = []
    for selected in frozen.itertuples(index=False):
        case_key = map_key.loc[(selected.symbol, selected.semantic_group_id, selected.timeframe)]
        pair = {"strategy_id": selected.strategy_id, "semantic_group_id": selected.semantic_group_id, "source_origin": selected.source_origin, "symbol": selected.symbol, "timeframe": selected.timeframe, "selection_reason": selected.selection_rule, "case_key": case_key, "maker_data_available": True}
        paths = {}
        for model, folder in (("FIRST_TICK_IDEALIZED", "01_FIRST_TICK"), ("GTC_UNTIL_SIGNAL_INVALID", "02_MAKER")):
            raw_metric = metric_lookup.loc[(selected.symbol, case_key, model)]
            normalized = normalize_metrics(raw_metric, "FIRST_TICK_IDEALIZED" if folder == "01_FIRST_TICK" else "MAKER")
            path = load_path(work, selected.symbol, case_key, "FIRST_TICK_IDEALIZED" if folder == "01_FIRST_TICK" else "MAKER")
            position = path.position.to_numpy(float) if folder == "01_FIRST_TICK" else path.actual_position.to_numpy(float)
            persistent_flag, persist = persistence(position)
            figure_rel = Path(folder) / selected.strategy_id / "performance" / selected.timeframe / f"{selected.symbol}__performance.png"
            render_detail(path, delivery / figure_rel, selected.strategy_id, selected.symbol, selected.timeframe, folder, normalized)
            tag = "FIRST_TICK" if folder == "01_FIRST_TICK" else "MAKER"
            pair[f"{tag}_figure"] = str(figure_rel).replace("\\", "/")
            for key, value in normalized.items():
                pair[f"{key}_{tag}"] = value
            pair[f"Persistent_{tag}"] = persistent_flag
            pair.update({f"{key}_{tag}": value for key, value in persist.items()})
            paths[tag] = normalized
        rows.append(pair)
    pairs = pd.DataFrame(rows)
    for strategy, group in pairs.groupby("strategy_id"):
        for timeframe in TIMEFRAMES:
            selected_tf = group[group.timeframe.eq(timeframe)]
            if selected_tf.empty:
                continue
            for tag, folder in (("FIRST_TICK", "01_FIRST_TICK"), ("MAKER", "02_MAKER")):
                summary = selected_tf[["symbol", f"Return_{tag}", f"BE_{tag}", f"Sharpe_{tag}", f"MaxDD_{tag}", f"Persistent_{tag}"]].rename(
                    columns={f"Return_{tag}":"Return", f"BE_{tag}":"BE", f"Sharpe_{tag}":"Sharpe", f"MaxDD_{tag}":"MaxDD", f"Persistent_{tag}":"Persistent"}
                )
                render_summary(summary, delivery / folder / strategy / f"summary_{timeframe}.png", strategy, timeframe, folder)
    atomic_csv(pairs, delivery / "execution_pair_index.csv")
    comparison = pairs.copy()
    for metric in ("Return", "Sharpe", "BE", "MaxDD", "Turnover"):
        comparison[f"Delta_{metric}"] = comparison[f"{metric}_MAKER"] - comparison[f"{metric}_FIRST_TICK"]
    atomic_csv(comparison, delivery / "execution_method_comparison.csv")
    build_provenance(repo, work, delivery)
    reverse_cases, validated = aggregate_reverse(work, delivery)
    render_reverse_figures(work, delivery, reverse_cases)
    reverse_maker_figures = render_reverse_maker_sensitivity(work, delivery, reverse_cases)
    atomic_json(
        {
            "status": "FROZEN_BEFORE_REVERSE_PERFORMANCE",
            "preferred_post_selection_start": "2026-07-01",
            "preferred_common_window_available_for_all_9_symbols": False,
            "validation_type": "INTERNAL_TEMPORAL_VALIDATION",
            "discovery": "[2024-07-01, 2025-07-01)",
            "validation": "[2025-07-01, 2026-06-30)",
            "selection_condition": "Return_NORMAL_DISCOVERY < 0",
            "validation_rule": "Return_REVERSE > 0 AND Sharpe_REVERSE > 0 AND Signed_BE_REVERSE > 0 AND Return_REVERSE > Return_NORMAL",
            "same_sample_claim": "EXPLORATORY_IN_SAMPLE_REVERSE_ONLY",
        },
        delivery / "reverse_validation/validation_window.json",
    )
    atomic_csv(
        pd.DataFrame(
            [
                {
                    "maker_reverse_status": "HISTORICAL_BBO_WINDOW_ONLY_NOT_VALIDATION_WINDOW",
                    "validation_window": "[2025-07-01, 2026-06-30)",
                    "available_L1_comparison_window": "[2024-03-01, 2024-03-31)",
                    "historical_maker_sensitivity_executed": reverse_maker_figures > 0,
                    "historical_maker_sensitivity_figures": reverse_maker_figures,
                    "MAKER_VALIDATED_REVERSE": False,
                    "reason": "L1 BBO maker data is not available on the frozen reverse-validation window; no cross-window maker-validity claim is made",
                }
            ]
        ),
        delivery / "reverse_validation/maker_reverse_validation_status.csv",
    )
    shutil.copy2(work / "selection/first_tick_selected_cases.csv", delivery / "selection/first_tick_selected_cases.csv")
    shutil.copy2(work / "selection/positive_be_priority.csv", delivery / "selection/positive_be_priority.csv")
    shutil.copy2(work / "selection/selection_freeze.json", delivery / "selection/selection_freeze.json")
    top_positive = frozen[frozen.Signed_BE_FIRST_TICK.gt(0)].sort_values(
        ["Signed_BE_FIRST_TICK", "Sharpe_FIRST_TICK", "Return_FIRST_TICK", "MaxDD_FIRST_TICK"],
        ascending=[False, False, False, False],
    ).head(20)
    top_positive_text = ";".join(
        f"{row.strategy_id}/{row.symbol}/{row.timeframe}:BE={row.Signed_BE_FIRST_TICK:.4f}"
        for row in top_positive.itertuples(index=False)
    )
    answers = pd.DataFrame(
        [
            ["FIRST_TICK selected cases", len(frozen)],
            ["Selected strategy IDs", frozen.strategy_id.nunique()],
            ["Selected WORKBOOK strategy IDs", frozen.loc[frozen.source_origin.eq("WORKBOOK"), "strategy_id"].nunique()],
            ["Selected PRE_WORKBOOK strategy IDs", frozen.loc[frozen.source_origin.eq("PRE_WORKBOOK"), "strategy_id"].nunique()],
            ["Largest positive FIRST_TICK BE selected cases", top_positive_text],
            ["Paired FIRST_TICK/MAKER cases", len(pairs)],
            ["Maker data unavailable selected cases", 0],
            ["Median Sharpe FIRST_TICK comparison window", pairs.Sharpe_FIRST_TICK.median()],
            ["Median Sharpe MAKER comparison window", pairs.Sharpe_MAKER.median()],
            ["Median BE FIRST_TICK comparison window", pairs.BE_FIRST_TICK.median()],
            ["Median BE MAKER comparison window", pairs.BE_MAKER.median()],
            ["Negative NORMAL cases reverse-tested", len(reverse_cases)],
            ["In-sample reverse positive", int((reverse_cases.Return_REVERSE_DISCOVERY > 0).sum())],
            ["Validation reverse positive", len(validated)],
            ["Validated reverse strategy IDs", ";".join(sorted(validated.strategy_id.unique()))],
            ["Maker BBO source", "Binance official historical USD-M Futures bookTicker -> Nautilus QuoteTick"],
            ["Maker trade source", "Binance official historical USD-M Futures trades -> Nautilus TradeTick"],
        ], columns=["question", "answer"]
    )
    atomic_csv(answers, delivery / "key_answers.csv")
    first_figures = len(list((delivery / "01_FIRST_TICK").rglob("*.png")))
    maker_figures = len(list((delivery / "02_MAKER").rglob("*.png")))
    if len(pairs) != len(frozen) or first_figures != maker_figures:
        raise ValueError("paired execution figure identity/count mismatch")
    if sha256(work / "selection/first_tick_selected_cases.csv") != freeze_hash:
        raise ValueError("frozen selection mutated")
    summary = {
        "status":"PASSED", "selected_cases":len(frozen), "selected_strategy_ids":frozen.strategy_id.nunique(),
        "paired_cases":len(pairs), "maker_data_unavailable":0, "first_tick_figures":first_figures,
        "maker_figures":maker_figures, "negative_normal_cases_reverse_tested":len(reverse_cases),
        "reverse_historical_maker_sensitivity_figures":reverse_maker_figures,
        "in_sample_reverse_positive":int((reverse_cases.Return_REVERSE_DISCOVERY > 0).sum()),
        "validation_reverse_positive":len(validated), "selection_manifest_sha256":freeze_hash,
        "maker_selected_new_cases":0, "parameter_search":0, "threshold_optimization":0,
        "comparison_window":"[2024-03-01, 2024-03-31)", "reverse_validation_type":"INTERNAL_TEMPORAL_VALIDATION",
    }
    atomic_json(summary, delivery / "validation_summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
