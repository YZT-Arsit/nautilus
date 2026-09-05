#!/usr/bin/env python3
"""Build the frozen Stage-A review for WORKBOOK + PRE_WORKBOOK strategies."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.build_10m15m_tick_be_sharpe_review import (  # noqa: E402
    SYMBOLS,
    atomic_csv,
    atomic_json,
    daily_sharpe,
    performance_figure,
    sha256,
)
from scripts.internal.build_boss_persistence_v2 import (  # noqa: E402
    directional_persistence_metrics,
)
from scripts.internal.run_stagea_preworkbook_tick_screen import (  # noqa: E402
    PREWORKBOOK_REGISTRY,
    canonical_preworkbook_scope,
)

TIMEFRAMES = ("1m", "10m", "15m")
WORKBOOK_SOURCE = Path(
    "outputs/deliverables/boss_multitimeframe_final_delivery/02_full_results"
)
WORKBOOK_CASES = Path(
    "outputs/baseline_evaluation/boss_multitimeframe_tick_screen/matrix_cases"
)
PREWORKBOOK_ROOT = Path(
    "outputs/baseline_evaluation/tick_review_stageA_9symbols_preworkbook"
)
OUTPUT = Path("outputs/deliverables/tick_review_stageA_9symbols")
TOLERANCE = {"Return": 1e-10, "Turnover": 1e-6, "MDD": 1e-10}


def workbook_case_path(repo: Path, semantic: str, symbol: str, timeframe: str) -> Path:
    return (
        repo / WORKBOOK_CASES / f"symbol={symbol}" / f"timeframe={timeframe}"
        / f"semantic={semantic}" / "review_timeseries.parquet"
    )


def preworkbook_case_root(repo: Path, strategy: str, symbol: str, timeframe: str) -> Path:
    return (
        repo / PREWORKBOOK_ROOT / "matrix_cases" / f"symbol={symbol}"
        / f"timeframe={timeframe}" / f"strategy={strategy}"
    )


def validate_series(frame: pd.DataFrame, expected: pd.Series) -> tuple[float, int, dict[str, float]]:
    required = {
        "event_time_ns", "cumulative_return_with_premium", "cumulative_turnover",
        "executed_position", "drawdown",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"review timeseries missing {sorted(missing)}")
    residuals = {
        "Return": abs(float(frame.cumulative_return_with_premium.iloc[-1]) - float(expected.Return_fee0)),
        "Turnover": abs(float(frame.cumulative_turnover.iloc[-1]) - float(expected.Turnover_raw)),
        "MDD": abs(float(frame.drawdown.min()) - float(expected.MDD)),
    }
    if any(residuals[key] > TOLERANCE[key] for key in residuals):
        raise ValueError(f"aggregate residual mismatch: {residuals}")
    sharpe, days = daily_sharpe(frame)
    return sharpe, days, residuals


def load_workbook(repo: Path) -> tuple[pd.DataFrame, dict[tuple[str, str, str], Path], dict[str, float]]:
    source = repo / WORKBOOK_SOURCE
    master = pd.read_csv(source / "boss_multitimeframe_tick_master.csv")
    master = master[master.timeframe.isin(TIMEFRAMES)].copy()
    if len(master) != 267 * len(SYMBOLS) * len(TIMEFRAMES):
        raise ValueError(f"WORKBOOK Stage-A rows changed: {len(master)}")
    persistence = pd.read_csv(source / "persistent_position_metrics_v2.csv")
    persistence = persistence[persistence.timeframe.isin(TIMEFRAMES)][[
        "strategy_id", "symbol", "timeframe", "directionally_persistent",
        "nonflat_fraction_v2", "long_fraction_v2", "short_fraction_v2",
        "flat_fraction_v2", "median_directional_run_hours",
        "P90_directional_run_hours", "sign_switches_per_day",
    ]]
    master = master.merge(
        persistence, on=["strategy_id", "symbol", "timeframe"], how="left",
        validate="one_to_one",
    )
    physical = master.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"])
    sharpe_rows: list[dict[str, object]] = []
    series_paths: dict[tuple[str, str, str], Path] = {}
    max_residuals = {"Return": 0.0, "Turnover": 0.0, "MDD": 0.0}
    for row in physical.itertuples(index=False):
        path = workbook_case_path(repo, str(row.semantic_execution_hash), row.symbol, row.timeframe)
        if not path.is_file():
            raise FileNotFoundError(f"WORKBOOK_REVIEW_TIMESERIES_MISSING: {path}")
        frame = pd.read_parquet(path)
        sharpe, days, residuals = validate_series(frame, pd.Series(row._asdict()))
        for key, value in residuals.items():
            max_residuals[key] = max(max_residuals[key], value)
        sharpe_rows.append({
            "semantic_execution_hash": row.semantic_execution_hash,
            "symbol": row.symbol, "timeframe": row.timeframe,
            "Sharpe": sharpe, "daily_observation_count": days,
        })
        series_paths[(str(row.semantic_execution_hash), row.symbol, row.timeframe)] = path
    master = master.merge(
        pd.DataFrame(sharpe_rows),
        on=["semantic_execution_hash", "symbol", "timeframe"],
        validate="many_to_one",
    )
    master["source_origin"] = "WORKBOOK"
    master["source_strategy_id"] = master.strategy_id.astype(str)
    master["semantic_group_id"] = master.semantic_execution_hash.astype(str)
    master["representative_strategy_id"] = master.groupby("semantic_execution_hash").strategy_id.transform("min")
    return normalize(master), series_paths, max_residuals


def load_preworkbook(repo: Path) -> tuple[pd.DataFrame, dict[tuple[str, str, str], Path], dict[str, float]]:
    scope = canonical_preworkbook_scope()
    strategies = sorted(scope.loc[scope.included, "strategy_name"].astype(str))
    rows: list[dict[str, object]] = []
    series_paths: dict[tuple[str, str, str], Path] = {}
    max_residuals = {"Return": 0.0, "Turnover": 0.0, "MDD": 0.0}
    for strategy in strategies:
        for symbol in SYMBOLS:
            for timeframe in TIMEFRAMES:
                case_root = preworkbook_case_root(repo, strategy, symbol, timeframe)
                summary_path = case_root / "summary.json"
                review_path = case_root / "review_timeseries.parquet"
                if not summary_path.is_file() or not review_path.is_file():
                    raise FileNotFoundError(f"PRE_WORKBOOK_CASE_MISSING: {case_root}")
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if summary.get("status") != "COMPLETED":
                    raise ValueError(f"PRE_WORKBOOK_CASE_NOT_COMPLETED: {summary_path}")
                frame = pd.read_parquet(review_path)
                expected = pd.Series(summary)
                sharpe, days, residuals = validate_series(frame, expected)
                for key, value in residuals.items():
                    max_residuals[key] = max(max_residuals[key], value)
                row = dict(summary)
                row.update({
                    "strategy_id": strategy,
                    "source_origin": "PRE_WORKBOOK",
                    "source_strategy_id": strategy,
                    "semantic_group_id": f"PRE_WORKBOOK:{strategy}",
                    "representative_strategy_id": strategy,
                    "Sharpe": sharpe,
                    "daily_observation_count": days,
                })
                rows.append(row)
                series_paths[(f"PRE_WORKBOOK:{strategy}", symbol, timeframe)] = review_path
    frame = pd.DataFrame(rows)
    if len(frame) != 64 * len(SYMBOLS) * len(TIMEFRAMES):
        raise ValueError(f"PRE_WORKBOOK Stage-A rows changed: {len(frame)}")
    return normalize(frame), series_paths, max_residuals


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["Return"] = pd.to_numeric(result.Return_fee0, errors="raise")
    be = result["Signed_BE_bps"] if "Signed_BE_bps" in result else result["BE_bps"]
    if "BE_bps" in result:
        be = be.fillna(result.BE_bps)
    result["Signed_BE_bps"] = pd.to_numeric(be, errors="coerce")
    result["Max_Drawdown"] = pd.to_numeric(result.MDD, errors="raise")
    result["Turnover_raw"] = pd.to_numeric(result.Turnover_raw, errors="raise")
    result["Turnover_pct"] = result.Turnover_raw * 100.0
    result["Persistent"] = result.directionally_persistent.astype(bool)
    result["Nonflat_fraction"] = result.nonflat_fraction_v2
    result["Long_fraction"] = result.long_fraction_v2
    result["Short_fraction"] = result.short_fraction_v2
    result["Flat_fraction"] = result.flat_fraction_v2
    result["Median_directional_run_hours"] = result.median_directional_run_hours
    result["P90_directional_run_hours"] = result.P90_directional_run_hours
    result["Switches_per_day"] = result.sign_switches_per_day
    return result


def add_selection(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["QUALIFY_1M"] = result.timeframe.eq("1m") & result.Sharpe.abs().gt(1.5)
    result["QUALIFY_10M15M"] = (
        result.timeframe.isin(["10m", "15m"])
        & result.Signed_BE_bps.abs().gt(10.0)
        & result.Sharpe.abs().gt(1.0)
    )
    result["CASE_QUALIFIES"] = result.QUALIFY_1M | result.QUALIFY_10M15M
    result["POSITIVE_SHARPE_1M"] = result.timeframe.eq("1m") & result.Sharpe.gt(1.5)
    result["POSITIVE_BE_SHARPE"] = (
        result.timeframe.isin(["10m", "15m"])
        & result.Signed_BE_bps.gt(10.0) & result.Sharpe.gt(1.0)
    )
    selected = set(result.loc[result.CASE_QUALIFIES, "strategy_id"].astype(str))
    result["STRATEGY_SELECTED"] = result.strategy_id.astype(str).isin(selected)
    return result


def heatmap(path: Path, strategy: str, timeframe: str, frame: pd.DataFrame) -> None:
    frame = frame.set_index("symbol").reindex(SYMBOLS)
    values = np.vstack([
        frame.Return.to_numpy(float) * 100,
        frame.Signed_BE_bps.to_numpy(float),
        frame.Sharpe.to_numpy(float),
        frame.Max_Drawdown.to_numpy(float) * 100,
        frame.Persistent.astype(float).to_numpy(),
    ])
    labels = ["Return (1x, %)", "Signed BE (bps)", "Sharpe", "Max DD (%)", "Persistent"]
    qualifies = (
        frame.Sharpe.abs().gt(1.5).to_numpy()
        if timeframe == "1m"
        else (frame.Signed_BE_bps.abs().gt(10) & frame.Sharpe.abs().gt(1)).to_numpy()
    )
    image = np.zeros_like(values)
    for index, row in enumerate(values):
        finite = row[np.isfinite(row)]
        if index == 4:
            image[index] = row
        elif len(finite):
            scale = max(abs(np.quantile(finite, 0.05)), abs(np.quantile(finite, 0.95)), 1e-12)
            image[index] = np.clip(row / scale, -1, 1)
    fig, ax = plt.subplots(figsize=(14, 4.6), constrained_layout=True)
    ax.imshow(image, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(SYMBOLS)), SYMBOLS, rotation=30, ha="right")
    ax.set_yticks(range(5), labels)
    for row_index in range(5):
        for symbol_index in range(len(SYMBOLS)):
            value = values[row_index, symbol_index]
            text = "NaN" if not np.isfinite(value) else (
                f"{int(value)}" if row_index == 4 else f"{value:.2f}"
            )
            if row_index == 2 and qualifies[symbol_index]:
                text += " Q"
            ax.text(symbol_index, row_index, text, ha="center", va="center", fontsize=8)
            if qualifies[symbol_index] and row_index in {1, 2}:
                ax.add_patch(Rectangle(
                    (symbol_index - 0.49, row_index - 0.49), 0.98, 0.98,
                    fill=False, edgecolor="#7e22ce", linewidth=3,
                ))
    ax.set_title(
        f"{strategy} | {timeframe} | 9-symbol frozen review\n"
        "bar signal → raw tick execution", fontsize=13,
    )
    ax.set_xticks(np.arange(-0.5, len(SYMBOLS), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, 5, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1)
    ax.tick_params(which="minor", bottom=False, left=False)
    rule = "Q = |Sharpe| > 1.5" if timeframe == "1m" else "Q = |BE| > 10 bps AND |Sharpe| > 1.0"
    fig.text(0.99, 0.01, rule, ha="right", fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def scope_table(repo: Path) -> pd.DataFrame:
    scope_path = (
        repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
        / "boss_multitimeframe_strategy_scope.csv"
    )
    if scope_path.is_file():
        workbook_scope = pd.read_csv(scope_path)
    else:
        canonical = pd.read_csv(
            repo / "outputs/deliverables/all_converted_workbook_strategies"
            / "all_converted_workbook_strategies.csv"
        )
        workbook_scope = canonical.loc[
            canonical.record_type.eq("STRATEGY_INDEX"),
            ["strategy_id", "canonical_timeframe"],
        ].copy()
        workbook_scope["eligible_for_intraday_resample"] = workbook_scope.canonical_timeframe.eq("1m")
        workbook_scope["exclusion_reason"] = np.where(
            workbook_scope.eligible_for_intraday_resample,
            "",
            "SOURCE_NATIVE_NON_1M_NOT_FORCED_TO_INTRADAY",
        )
    workbook_scope["source_origin"] = "WORKBOOK"
    workbook_scope["eligible_1m"] = (
        workbook_scope.eligible_for_intraday_resample.astype(str).str.lower().isin(["true", "1"])
    )
    workbook_scope["eligible_10m"] = workbook_scope.eligible_1m
    workbook_scope["eligible_15m"] = workbook_scope.eligible_1m
    workbook_scope = workbook_scope.rename(columns={"strategy_id": "strategy_id"})
    pre = canonical_preworkbook_scope().rename(columns={"strategy_name": "strategy_id"})
    columns = [
        "strategy_id", "source_origin", "eligible_1m", "eligible_10m",
        "eligible_15m", "exclusion_reason",
    ]
    return pd.concat([workbook_scope[columns], pre[columns]], ignore_index=True)


def availability_preview(repo: Path) -> pd.DataFrame:
    market = repo / "historical_data/market_data"
    compact = (
        repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
        / "tick_execution_index"
    )
    symbols: set[str] = set(SYMBOLS)
    if market.is_dir():
        symbols.update(path.name.split("=", 1)[1] for path in market.glob(
            "asset_class=crypto/exchange=BINANCE/venue_type=futures_um/symbol=*"
        ))
    rows = []
    for symbol in sorted(symbols):
        base = market / "asset_class=crypto/exchange=BINANCE/venue_type=futures_um" / f"symbol={symbol}"
        bar = base / "data_type=bar" / "freq=1m"
        funding = base / "data_type=funding_rate" / "freq=settlement"
        tick = base / "data_type=trade" / "freq=tick"
        compact_symbol = compact / f"symbol={symbol}"

        def dates(path: Path) -> tuple[str, str]:
            values = sorted(item.name.split("=", 1)[1] for item in path.glob("date=*") if item.is_dir())
            return (values[0], values[-1]) if values else ("", "")

        first_bar, last_bar = dates(bar)
        first_funding, last_funding = dates(funding)
        first_tick, last_tick = dates(tick)
        if not first_tick:
            first_tick, last_tick = dates(compact_symbol)
        eligible = bool(first_bar and first_funding and first_tick)
        rows.append({
            "symbol": symbol,
            "1m_bar_available": bool(first_bar),
            "funding_available": bool(first_funding),
            "canonical_tick_available": tick.is_dir(),
            "compact_tick_index_available": compact_symbol.is_dir(),
            "first_bar_date": first_bar, "last_bar_date": last_bar,
            "first_funding_date": first_funding, "last_funding_date": last_funding,
            "first_tick_date": first_tick, "last_tick_date": last_tick,
            "stageB_candidate": eligible,
            "exclusion_reason": "" if eligible else "MISSING_PROJECT_COMPATIBLE_BAR_FUNDING_OR_TICK_DATA",
        })
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = (args.output or repo / OUTPUT).resolve()

    workbook, workbook_paths, workbook_residuals = load_workbook(repo)
    pre, pre_paths, pre_residuals = load_preworkbook(repo)
    all_results = add_selection(pd.concat([workbook, pre], ignore_index=True))
    if len(all_results) != (267 + 64) * len(SYMBOLS) * len(TIMEFRAMES):
        raise ValueError("expanded Stage-A logical population mismatch")
    if all_results.strategy_id.nunique() != 331:
        raise ValueError("expanded Stage-A strategy identity mismatch")
    if all_results.duplicated(["strategy_id", "symbol", "timeframe"]).any():
        raise ValueError("duplicate logical Stage-A case")

    selected_ids = set(all_results.loc[all_results.CASE_QUALIFIES, "strategy_id"].astype(str))
    selected = all_results[all_results.strategy_id.astype(str).isin(selected_ids)].copy()
    qualifying = all_results[all_results.CASE_QUALIFIES].copy()
    qualifying["qualification_type"] = np.where(
        qualifying.timeframe.eq("1m"), "1M_SHARPE",
        np.where(qualifying.timeframe.eq("10m"), "10M_BE_SHARPE", "15M_BE_SHARPE"),
    )
    series_paths = {**workbook_paths, **pre_paths}

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    all_columns = [
        "strategy_id", "source_origin", "source_strategy_id", "semantic_group_id",
        "representative_strategy_id", "symbol", "timeframe", "Return", "Sharpe",
        "Signed_BE_bps", "Max_Drawdown", "Turnover_raw", "Turnover_pct", "Persistent",
        "Nonflat_fraction", "Long_fraction", "Short_fraction", "Flat_fraction",
        "Median_directional_run_hours", "P90_directional_run_hours", "Switches_per_day",
        "QUALIFY_1M", "QUALIFY_10M15M", "POSITIVE_SHARPE_1M",
        "POSITIVE_BE_SHARPE", "STRATEGY_SELECTED",
    ]
    atomic_csv(output / "all_1m10m15m_results.csv", all_results[all_columns])
    q_columns = [
        "strategy_id", "source_origin", "source_strategy_id", "semantic_group_id",
        "symbol", "timeframe", "Return", "Sharpe", "Signed_BE_bps",
        "Max_Drawdown", "Turnover_pct", "Persistent", "qualification_type",
    ]
    qualifying["performance_figure_path"] = [
        str(PurePosixPath("strategies") / row.strategy_id / "performance" / row.timeframe / f"{row.symbol}__performance.png")
        for row in qualifying.itertuples()
    ]
    atomic_csv(output / "qualifying_cases.csv", qualifying[q_columns + ["performance_figure_path"]])
    atomic_csv(output / "qualifying_1m_sharpe_cases.csv", qualifying[qualifying.timeframe.eq("1m")][q_columns + ["performance_figure_path"]])
    atomic_csv(output / "qualifying_10m15m_be_sharpe_cases.csv", qualifying[qualifying.timeframe.isin(["10m", "15m"])][q_columns + ["performance_figure_path"]])
    atomic_csv(output / "pre_workbook_results.csv", all_results[all_results.source_origin.eq("PRE_WORKBOOK")][all_columns])
    atomic_csv(output / "strategy_scope.csv", scope_table(repo))
    preview = availability_preview(repo)
    atomic_csv(output / "available_symbol_pool_preview.csv", preview)

    strategy_rows = []
    index_rows = []
    summary_columns = all_columns[:-5] + ["QUALIFY_1M", "QUALIFY_10M15M", "STRATEGY_SELECTED"]
    tf_type = pd.CategoricalDtype(TIMEFRAMES, ordered=True)
    symbol_type = pd.CategoricalDtype(SYMBOLS, ordered=True)
    for strategy in sorted(selected_ids):
        group = selected[selected.strategy_id.astype(str).eq(strategy)].copy()
        group["timeframe"] = group.timeframe.astype(tf_type)
        group["symbol"] = group.symbol.astype(symbol_type)
        group = group.sort_values(["timeframe", "symbol"])
        if len(group) != 27:
            raise ValueError(f"selected strategy coverage incomplete: {strategy}")
        folder = output / "strategies" / strategy
        atomic_csv(folder / "summary.csv", group[summary_columns])
        for timeframe in TIMEFRAMES:
            heatmap(folder / f"summary_{timeframe}.png", strategy, timeframe, group[group.timeframe.eq(timeframe)])
        q = qualifying[qualifying.strategy_id.astype(str).eq(strategy)]
        by_tf = {tf: q[q.timeframe.eq(tf)] for tf in TIMEFRAMES}
        row = {
            "strategy_id": strategy,
            "source_origin": group.source_origin.iloc[0],
            "source_strategy_id": group.source_strategy_id.iloc[0],
            "semantic_group_id": group.semantic_group_id.iloc[0],
            "selected_by_1m": bool(len(by_tf["1m"])),
            "selected_by_10m": bool(len(by_tf["10m"])),
            "selected_by_15m": bool(len(by_tf["15m"])),
            "qualifying_1m_case_count": len(by_tf["1m"]),
            "qualifying_10m_case_count": len(by_tf["10m"]),
            "qualifying_15m_case_count": len(by_tf["15m"]),
            "qualifying_symbols_1m": ";".join(by_tf["1m"].symbol.astype(str)),
            "qualifying_symbols_10m": ";".join(by_tf["10m"].symbol.astype(str)),
            "qualifying_symbols_15m": ";".join(by_tf["15m"].symbol.astype(str)),
            "max_abs_Sharpe_1m": float(group[group.timeframe.eq("1m")].Sharpe.abs().max()),
            "max_abs_BE_10m15m": float(group[group.timeframe.isin(["10m", "15m"])].Signed_BE_bps.abs().max()),
            "max_abs_Sharpe_10m15m": float(group[group.timeframe.isin(["10m", "15m"])].Sharpe.abs().max()),
            "summary_1m": str(PurePosixPath("strategies") / strategy / "summary_1m.png"),
            "summary_10m": str(PurePosixPath("strategies") / strategy / "summary_10m.png"),
            "summary_15m": str(PurePosixPath("strategies") / strategy / "summary_15m.png"),
            "detailed_figure_count": len(q),
            "strategy_folder": str(PurePosixPath("strategies") / strategy),
        }
        strategy_rows.append(row)
        index_rows.append({
            "strategy_id": strategy, "source_origin": row["source_origin"],
            "semantic_group_id": row["semantic_group_id"],
            "qualifying_case_count": len(q),
            "summary_1m": row["summary_1m"], "summary_10m": row["summary_10m"],
            "summary_15m": row["summary_15m"],
            "detailed_performance_count": len(q), "folder": row["strategy_folder"],
        })

    strategies_frame = pd.DataFrame(strategy_rows)
    atomic_csv(output / "qualifying_strategies.csv", strategies_frame)
    atomic_csv(output / "strategy_index.csv", pd.DataFrame(index_rows))
    atomic_csv(output / "pre_workbook_qualifying_strategies.csv", strategies_frame[strategies_frame.source_origin.eq("PRE_WORKBOOK")])

    for row in qualifying.itertuples(index=False):
        path = series_paths[(str(row.semantic_group_id), row.symbol, row.timeframe)]
        series = pd.read_parquet(path)
        performance_figure(
            output / Path(row.performance_figure_path), row.strategy_id,
            pd.Series(row._asdict()), series,
        )

    origin_rows = []
    for origin in ["WORKBOOK", "PRE_WORKBOOK", "TOTAL"]:
        frame = all_results if origin == "TOTAL" else all_results[all_results.source_origin.eq(origin)]
        q = frame[frame.CASE_QUALIFIES]
        origin_rows.append({
            "source_origin": origin,
            "strategies_audited": frame.strategy_id.nunique(),
            "independent_semantic_groups_audited": frame.semantic_group_id.nunique(),
            "qualifying_strategies": q.strategy_id.nunique(),
            "qualifying_1m_cases": int(q.timeframe.eq("1m").sum()),
            "qualifying_10m_cases": int(q.timeframe.eq("10m").sum()),
            "qualifying_15m_cases": int(q.timeframe.eq("15m").sum()),
        })
    atomic_csv(output / "source_origin_summary.csv", pd.DataFrame(origin_rows))

    selected_by_1m = strategies_frame.qualifying_1m_case_count.gt(0)
    selected_by_slow = strategies_frame.qualifying_10m_case_count.add(strategies_frame.qualifying_15m_case_count).gt(0)
    validation = {
        "status": "PASSED",
        "stage_status": "READY_FOR_USER_REVIEW",
        "workbook_strategy_ids": 267,
        "pre_workbook_strategy_ids": 64,
        "total_strategy_ids": 331,
        "independent_semantic_groups": int(all_results.semantic_group_id.nunique()),
        "logical_cases": len(all_results),
        "qualifying_1m_cases": int(qualifying.timeframe.eq("1m").sum()),
        "qualifying_10m_cases": int(qualifying.timeframe.eq("10m").sum()),
        "qualifying_15m_cases": int(qualifying.timeframe.eq("15m").sum()),
        "workbook_qualifying_strategies": int(strategies_frame.source_origin.eq("WORKBOOK").sum()),
        "pre_workbook_qualifying_strategies": int(strategies_frame.source_origin.eq("PRE_WORKBOOK").sum()),
        "total_qualifying_strategies": len(strategies_frame),
        "selected_by_1m_only": int((selected_by_1m & ~selected_by_slow).sum()),
        "selected_by_10m15m_only": int((~selected_by_1m & selected_by_slow).sum()),
        "selected_by_both": int((selected_by_1m & selected_by_slow).sum()),
        "positive_1m_sharpe_cases": int(all_results.POSITIVE_SHARPE_1M.sum()),
        "positive_10m15m_be_sharpe_cases": int(all_results.POSITIVE_BE_SHARPE.sum()),
        "summary_figures": len(strategies_frame) * 3,
        "detailed_1m_figures": int(qualifying.timeframe.eq("1m").sum()),
        "detailed_10m_figures": int(qualifying.timeframe.eq("10m").sum()),
        "detailed_15m_figures": int(qualifying.timeframe.eq("15m").sum()),
        "stageB_available_symbol_candidates": int(preview.stageB_candidate.sum()),
        "workbook_max_residuals": workbook_residuals,
        "pre_workbook_max_residuals": pre_residuals,
        "unaccounted_executable_strategy_ids": 0,
        "five_bp_columns": 0,
        "workbook_backtests_rerun": 0,
        "tick_index_rebuild": 0,
        "parameter_optimization": 0,
        "strategy_semantic_changes": 0,
        "stageB_started": False,
    }
    atomic_json(output / "validation_summary.json", validation)
    zip_path = output.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with ZipFile(zip_path, "w", ZIP_DEFLATED, allowZip64=True) as archive:
        for file in sorted(output.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(output.parent))
    with ZipFile(zip_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Stage-A ZIP integrity failed")
    validation["zip_path"] = str(zip_path)
    validation["zip_sha256"] = sha256(zip_path)
    atomic_json(output / "validation_summary.json", validation)
    print(json.dumps(validation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
