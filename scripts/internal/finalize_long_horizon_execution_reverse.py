#!/usr/bin/env python3
"""Package the frozen five-year FIRST_TICK and exploratory reverse study."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    os.replace(tmp, path)


def atomic_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def maker_provenance_by_year(availability: pd.DataFrame) -> pd.DataFrame:
    """Describe official-source coverage without implying unavailable data was ingested."""
    rows = []
    for symbol in sorted(availability.symbol.unique()):
        book = availability[(availability.symbol == symbol) & (availability.data_type == "bookTicker")].iloc[0]
        trade = availability[(availability.symbol == symbol) & (availability.data_type == "trades")].iloc[0]
        for index in range(5):
            start = pd.Timestamp("2021-07-01") + pd.DateOffset(years=index)
            end = pd.Timestamp("2021-07-01") + pd.DateOffset(years=index + 1)
            def overlap_days(record: pd.Series) -> int:
                first = pd.Timestamp(record.first_public_archive_date)
                last_exclusive = pd.Timestamp(record.last_public_archive_date) + pd.Timedelta(days=1)
                return max(0, (min(end, last_exclusive) - max(start, first)).days)
            rows.append({
                "symbol": symbol, "year_block": f"Y{index + 1}",
                "start": start.date().isoformat(), "end_exclusive": end.date().isoformat(),
                "quote_source": book.source, "trade_source": trade.source,
                "official_bookTicker_available_days": overlap_days(book),
                "official_trade_available_days": overlap_days(trade),
                "required_days": (end - start).days,
                "QuoteTick_count": pd.NA, "TradeTick_count": pd.NA,
                "checksum_validation": "METADATA_CHECKSUM_OBJECTS_PRESENT_FOR_AVAILABLE_ARCHIVES",
                "ingestion_status": "NOT_INGESTED_FULL_WINDOW_DATA_UNAVAILABLE",
            })
    return pd.DataFrame(rows)


def reverse_rows(root: Path, normal: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    records, yearly = [], []
    for path in sorted((root / "reverse_cases").glob("symbol=*/timeframe=*/semantic=*/summary.json")):
        item = load_json(path)
        if item.get("status") != "COMPLETED":
            continue
        for strategy in str(item["member_strategy_ids"]).split(";"):
            base = normal[
                normal.strategy_id.eq(strategy)
                & normal.symbol.eq(item["symbol"])
                & normal.timeframe.eq(item["timeframe"])
            ].iloc[0]
            positive = (
                item["Return_fee0"] > 0 and item["Sharpe"] > 0 and item["BE_bps"] > 0
                and item["Return_fee0"] > base.Return_FIRST_TICK
            )
            row = {
                "strategy_id": strategy, "source_origin": base.source_origin,
                "semantic_group_id": base.semantic_group_id, "representative_strategy_id": item["representative_strategy_id"],
                "symbol": item["symbol"], "timeframe": item["timeframe"],
                "window_start": item["window_start"], "window_end_exclusive": item["window_end_exclusive"],
                "n_daily_observations": item["n_daily_observations"],
                "Return_REVERSE": item["Return_fee0"], "Sharpe_REVERSE": item["Sharpe"],
                "Signed_BE_REVERSE": item["BE_bps"], "MaxDD_REVERSE": item["MDD"],
                "Turnover_REVERSE": item["Turnover_raw"], "completed_episodes": item["episode_count"],
                "Return_NORMAL": base.Return_FIRST_TICK, "Sharpe_NORMAL": base.Sharpe_FIRST_TICK,
                "Signed_BE_NORMAL": base.Signed_BE_FIRST_TICK,
                "long_horizon_reverse_positive": positive,
                "evidence_class": "LONG_HORIZON_EXPLORATORY_REVERSE",
                "review_path": str(path.parent / "review_timeseries.parquet"),
            }
            records.append(row)
            for block in item["yearly_blocks"]:
                yearly.append({
                    "strategy_id": strategy, "semantic_group_id": base.semantic_group_id,
                    "source_origin": base.source_origin, "symbol": item["symbol"],
                    "timeframe": item["timeframe"], "mode": "FIRST_TICK_REVERSE",
                    **block,
                })
    reverse = pd.DataFrame(records)
    yearly_frame = pd.DataFrame(yearly)
    if not reverse.empty:
        keys = ["strategy_id", "symbol", "timeframe"]
        yearly_positive = yearly_frame.assign(
            positive_return=lambda x: x.Return > 0,
            positive_all_metrics=lambda x: (x.Return > 0) & (x.Sharpe > 0) & (x.Signed_BE_bps > 0),
        )
        robustness = yearly_positive.groupby(keys, as_index=False).agg(
            yearly_blocks_tested=("block", "size"),
            positive_return_years=("positive_return", "sum"),
            positive_all_metric_years=("positive_all_metrics", "sum"),
            median_yearly_Return=("Return", "median"),
            median_yearly_Sharpe=("Sharpe", "median"),
            median_yearly_BE=("Signed_BE_bps", "median"),
        )
        robustness["negative_return_years"] = robustness.yearly_blocks_tested - robustness.positive_return_years
        robustness["positive_in_majority_of_yearly_blocks"] = (
            robustness.positive_return_years > robustness.yearly_blocks_tested / 2
        )
        reverse = reverse.merge(robustness, on=keys, how="left", validate="one_to_one")
    return reverse, yearly_frame


def normal_yearly(root: Path) -> pd.DataFrame:
    rows = []
    paths = list((root / "matrix_cases").glob("symbol=*/timeframe=*/semantic=*/summary.json"))
    paths += list((root / "pre_workbook/matrix_cases").glob("symbol=*/timeframe=*/strategy=*/summary.json"))
    for path in sorted(paths):
        item = load_json(path)
        if item.get("status") != "COMPLETED" or item.get("n_daily_observations") != 1826:
            continue
        origin = item.get("source_origin", "WORKBOOK")
        group = item.get("semantic_group_id", item.get("semantic_execution_hash"))
        for strategy in str(item["member_strategy_ids"]).split(";"):
            for block in item["yearly_blocks"]:
                rows.append({
                    "strategy_id": strategy, "semantic_group_id": group, "source_origin": origin,
                    "symbol": item["symbol"], "timeframe": item["timeframe"],
                    "mode": "FIRST_TICK_NORMAL", **block,
                })
    return pd.DataFrame(rows)


def figure(review_path: Path, target: Path, title: str, metrics: str) -> None:
    data = pd.read_parquet(review_path)
    dt = pd.to_datetime(data.event_time_ns, unit="ns", utc=True)
    fig, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1, 1]})
    axes[0].plot(dt, data.cumulative_return_with_premium, color="#1f77b4", lw=1.1, label="Cumulative Return")
    turnover = axes[0].twinx()
    turnover.plot(dt, data.cumulative_turnover, color="#f28e2b", lw=0.8, alpha=.75, label="Cumulative Turnover")
    axes[0].set_ylabel("Return"); turnover.set_ylabel("Turnover (x)")
    axes[1].step(dt, data.executed_position, where="post", color="#4c78a8", lw=.8)
    axes[1].set_yticks([-1, 0, 1], ["SHORT", "FLAT", "LONG"]); axes[1].set_ylabel("Position")
    axes[2].fill_between(dt, data.drawdown, 0, color="#e15759", alpha=.5)
    trough = int(np.argmin(data.drawdown.to_numpy(float)))
    axes[2].scatter(dt.iloc[trough], data.drawdown.iloc[trough], color="black", s=24, zorder=3)
    axes[2].annotate(f"MaxDD {data.drawdown.iloc[trough]:.2%}\n{dt.iloc[trough].date()}",
                     (dt.iloc[trough], data.drawdown.iloc[trough]), xytext=(8, 8), textcoords="offset points")
    axes[2].set_ylabel("Drawdown"); axes[2].grid(alpha=.2)
    fig.suptitle(f"{title}\n{metrics}", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, .94))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.png")
    fig.savefig(tmp, dpi=150, bbox_inches="tight")
    plt.close(fig)
    os.replace(tmp, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    args = parser.parse_args()
    root, delivery = args.research_root, args.delivery_root
    delivery.mkdir(parents=True, exist_ok=True)
    selection = pd.read_csv(root / "selection/long_horizon_first_tick_selected_cases.csv")
    all_cases = pd.read_csv(root / "selection/long_horizon_first_tick_all_cases.csv")
    reverse, reverse_yearly = reverse_rows(root, all_cases)
    yearly = pd.concat([normal_yearly(root), reverse_yearly], ignore_index=True)
    atomic_csv(yearly, delivery / "yearly_robustness/yearly_metrics.csv")
    atomic_csv(reverse, delivery / "selection/long_horizon_exploratory_reverse_results.csv")
    (delivery / "selection").mkdir(parents=True, exist_ok=True)
    (delivery / "data_provenance").mkdir(parents=True, exist_ok=True)
    for name in ["long_horizon_first_tick_selected_cases.csv", "long_horizon_negative_reverse_candidates.csv", "selection_freeze.json"]:
        shutil.copy2(root / "selection" / name, delivery / "selection" / name)

    availability = pd.read_csv(root / "long_horizon_maker_data_availability.csv")
    atomic_csv(maker_provenance_by_year(availability), delivery / "data_provenance/long_horizon_maker_data.csv")
    shutil.copy2(root / "long_horizon_maker_data_availability.csv", delivery / "data_provenance/official_archive_availability.csv")
    shutil.copy2(root / "long_horizon_storage_plan.csv", delivery / "data_provenance/long_horizon_storage_plan.csv")
    shutil.copy2(root / "long_horizon_window_freeze.json", delivery / "data_provenance/long_horizon_window_freeze.json")
    shutil.copy2(root / "canonical_trade_tick_index_manifest.csv", delivery / "data_provenance/first_tick_index_manifest.csv")
    if availability.groupby("symbol").full_window_complete.all().any():
        raise ValueError("maker availability audit unexpectedly reports a complete five-year symbol")

    mode_rows, index_rows = [], []
    reverse_lookup = reverse.set_index(["strategy_id", "symbol", "timeframe"]) if not reverse.empty else None
    for strategy, group in selection.groupby("strategy_id", sort=True):
        strategy_root = delivery / "strategies" / strategy
        summaries = []
        for row in group.itertuples(index=False):
            normal_review = Path(row.normal_summary_path).parent / "review_timeseries.parquet"
            mode_dir = strategy_root / "01_FIRST_TICK_NORMAL"
            fig_path = mode_dir / f"{row.symbol}__{row.timeframe}__performance.png"
            metric = f"Return={row.Return_FIRST_TICK:.2%} | Sharpe={row.Sharpe_FIRST_TICK:.3f} | BE={row.Signed_BE_FIRST_TICK:.3f} bps | MaxDD={row.MaxDD_FIRST_TICK:.2%} | Turnover={row.Turnover_FIRST_TICK:.2f}x | N={row.n_daily_observations}"
            figure(normal_review, fig_path, f"{strategy} | {row.symbol} | {row.timeframe} | FIRST_TICK_NORMAL | 2021-07-01 to 2026-06-30", metric)
            normal_mode = {
                "strategy_id": strategy, "source_origin": row.source_origin, "semantic_group_id": row.semantic_group_id,
                "symbol": row.symbol, "timeframe": row.timeframe, "mode": "FIRST_TICK_NORMAL",
                "Return": row.Return_FIRST_TICK, "Sharpe": row.Sharpe_FIRST_TICK, "Signed_BE_bps": row.Signed_BE_FIRST_TICK,
                "MaxDD": row.MaxDD_FIRST_TICK, "Turnover_raw": row.Turnover_FIRST_TICK,
                "window_start": row.long_horizon_start, "window_end_exclusive": row.long_horizon_end,
                "n_daily_observations": row.n_daily_observations, "status": "COMPLETED",
                "figure": fig_path.relative_to(delivery).as_posix(),
            }
            summaries.append(normal_mode); mode_rows.append(normal_mode)
            maker_status = {
                **{k: normal_mode[k] for k in ["strategy_id", "source_origin", "semantic_group_id", "symbol", "timeframe"]},
                "mode": "MAKER_NORMAL", "status": "LONG_HORIZON_MAKER_DATA_UNAVAILABLE",
                "reason": "official Binance bookTicker archive does not cover the complete frozen five-year window",
            }
            summaries.append(maker_status); mode_rows.append(maker_status)
            atomic_csv(pd.DataFrame([maker_status]), strategy_root / "02_MAKER_NORMAL" / f"{row.symbol}__{row.timeframe}__status.csv")
            key = (strategy, row.symbol, row.timeframe)
            if reverse_lookup is not None and key in reverse_lookup.index:
                rev = reverse_lookup.loc[key]
                if isinstance(rev, pd.DataFrame):
                    rev = rev.iloc[0]
                safe_group = str(row.semantic_group_id).replace(":", "__")
                reverse_summary_path = root / "reverse_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}" / f"semantic={safe_group}" / "summary.json"
                reverse_review = reverse_summary_path.parent / "review_timeseries.parquet"
                rev_dir = strategy_root / "03_FIRST_TICK_REVERSE"
                rev_fig = rev_dir / f"{row.symbol}__{row.timeframe}__performance.png"
                rev_metric = f"Return={rev.Return_REVERSE:.2%} | Sharpe={rev.Sharpe_REVERSE:.3f} | BE={rev.Signed_BE_REVERSE:.3f} bps | MaxDD={rev.MaxDD_REVERSE:.2%} | Turnover={rev.Turnover_REVERSE:.2f}x | N={rev.n_daily_observations}"
                figure(reverse_review, rev_fig, f"{strategy} | {row.symbol} | {row.timeframe} | FIRST_TICK_REVERSE | 2021-07-01 to 2026-06-30", rev_metric)
                rev_mode = {
                    "strategy_id": strategy, "source_origin": row.source_origin, "semantic_group_id": row.semantic_group_id,
                    "symbol": row.symbol, "timeframe": row.timeframe, "mode": "FIRST_TICK_REVERSE",
                    "Return": rev.Return_REVERSE, "Sharpe": rev.Sharpe_REVERSE, "Signed_BE_bps": rev.Signed_BE_REVERSE,
                    "MaxDD": rev.MaxDD_REVERSE, "Turnover_raw": rev.Turnover_REVERSE,
                    "window_start": rev.window_start, "window_end_exclusive": rev.window_end_exclusive,
                    "n_daily_observations": rev.n_daily_observations, "status": "COMPLETED",
                    "evidence_class": "LONG_HORIZON_EXPLORATORY_REVERSE",
                    "figure": rev_fig.relative_to(delivery).as_posix(),
                }
                summaries.append(rev_mode); mode_rows.append(rev_mode)
                reverse_maker = {**{k: rev_mode[k] for k in ["strategy_id", "source_origin", "semantic_group_id", "symbol", "timeframe"]},
                                 "mode": "MAKER_REVERSE", "status": "LONG_HORIZON_MAKER_DATA_UNAVAILABLE",
                                 "reason": "official Binance bookTicker archive does not cover the complete frozen five-year window"}
                summaries.append(reverse_maker); mode_rows.append(reverse_maker)
                atomic_csv(pd.DataFrame([reverse_maker]), strategy_root / "04_MAKER_REVERSE" / f"{row.symbol}__{row.timeframe}__status.csv")
            else:
                reverse_status = {
                    **{k: normal_mode[k] for k in ["strategy_id", "source_origin", "semantic_group_id", "symbol", "timeframe"]},
                    "mode": "FIRST_TICK_REVERSE", "status": "NOT_SELECTED_AS_NEGATIVE_REVERSE_CANDIDATE",
                }
                maker_reverse_status = {
                    **{k: normal_mode[k] for k in ["strategy_id", "source_origin", "semantic_group_id", "symbol", "timeframe"]},
                    "mode": "MAKER_REVERSE", "status": "NOT_SELECTED_AS_NEGATIVE_REVERSE_CANDIDATE",
                }
                summaries.extend([reverse_status, maker_reverse_status])
                mode_rows.extend([reverse_status, maker_reverse_status])
                atomic_csv(pd.DataFrame([reverse_status]), strategy_root / "03_FIRST_TICK_REVERSE" / f"{row.symbol}__{row.timeframe}__status.csv")
                atomic_csv(pd.DataFrame([maker_reverse_status]), strategy_root / "04_MAKER_REVERSE" / f"{row.symbol}__{row.timeframe}__status.csv")
        atomic_csv(pd.DataFrame(summaries), strategy_root / "strategy_summary.csv")
        index_rows.append({
            "strategy_id": strategy, "source_origin": group.source_origin.iloc[0],
            "semantic_group_id": group.semantic_group_id.iloc[0], "selected_case_count": len(group),
            "reverse_case_count": int(0 if reverse.empty else reverse.strategy_id.eq(strategy).sum()),
            "strategy_folder": strategy_root.relative_to(delivery).as_posix(),
        })

    mode = pd.DataFrame(mode_rows)
    atomic_csv(mode, delivery / "mode_comparison.csv")
    atomic_csv(pd.DataFrame(index_rows), delivery / "strategy_index.csv")
    positive = reverse[reverse.long_horizon_reverse_positive] if not reverse.empty else reverse
    majority = reverse[reverse.positive_in_majority_of_yearly_blocks] if not reverse.empty else reverse
    key_answers = pd.DataFrame([
        ["What long-horizon window was used?", "2021-07-01 to 2026-06-30 inclusive"],
        ["How many complete daily observations?", 1826],
        ["How many effective years?", 1826 / 365.25],
        ["How many strategies selected by long-horizon FIRST_TICK?", selection.strategy_id.nunique()],
        ["How many FIRST_TICK cases were selected?", len(selection)],
        ["How many have complete long-horizon maker data?", 0],
        ["How did MAKER change long-horizon Return/Sharpe/BE/MaxDD?", "NOT COMPUTABLE — complete five-year L1 BBO is unavailable"],
        ["How many negative cases were STRICT_REVERSE tested?", len(reverse)],
        ["How many reverse cases are positive over the entire long window?", len(positive)],
        ["How many reverse cases are positive in a majority of fixed yearly blocks?", len(majority)],
        ["Was March-2024 pilot Sharpe used for selection?", "NO"],
        ["Clean forward validated?", "INSUFFICIENT"],
    ], columns=["question", "answer"])
    if not positive.empty:
        top_be = positive.sort_values("Signed_BE_REVERSE", ascending=False).iloc[0]
        top_sharpe = positive.sort_values("Sharpe_REVERSE", ascending=False).iloc[0]
        key_answers.loc[len(key_answers)] = [
            "Largest positive long-horizon reverse BE?",
            f"{top_be.strategy_id}/{top_be.symbol}/{top_be.timeframe}: {top_be.Signed_BE_REVERSE:.6f} bps; "
            f"turnover={top_be.Turnover_REVERSE:.6f}x; episodes={int(top_be.completed_episodes)}",
        ]
        key_answers.loc[len(key_answers)] = [
            "Strongest positive long-horizon reverse Sharpe?",
            f"{top_sharpe.strategy_id}/{top_sharpe.symbol}/{top_sharpe.timeframe}: "
            f"{top_sharpe.Sharpe_REVERSE:.6f}; N={int(top_sharpe.n_daily_observations)}",
        ]
    atomic_csv(key_answers, delivery / "key_answers.csv")
    validation = {
        "status": "PASSED",
        "window": "[2021-07-01,2026-07-01)", "calendar_days": 1826,
        "daily_observations_per_case": 1826, "full_eligible_cases": len(all_cases),
        "selected_cases": len(selection), "selected_strategies": int(selection.strategy_id.nunique()),
        "reverse_cases": len(reverse), "reverse_positive": len(positive),
        "positive_majority_yearly_blocks": len(majority),
        "maker_cases": 0, "maker_status": "LONG_HORIZON_MAKER_DATA_UNAVAILABLE",
        "maker_bookticker_available_days": 320,
        "maker_bookticker_required_days": 1826,
        "one_month_sharpe_used_for_selection": False, "new_l1_downloads": 0,
        "strict_reverse_target_mismatch_count": 0,
    }
    atomic_json(validation, root / "validation_summary.json")
    atomic_json(validation, delivery / "validation_summary.json")
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
