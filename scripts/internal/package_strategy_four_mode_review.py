#!/usr/bin/env python3
"""Package the frozen NORMAL-selected universe with all four execution modes."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_selected_partial_window_maker import END as MAKER_END
from scripts.internal.run_selected_partial_window_maker import START as MAKER_START
from scripts.internal.run_selected_partial_window_maker import case_key


MODES = ("01_FIRST_TICK_NORMAL", "02_MAKER_NORMAL", "03_FIRST_TICK_REVERSE", "04_MAKER_REVERSE")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sample(frame: pd.DataFrame, maximum: int = 12000) -> pd.DataFrame:
    if len(frame) <= maximum:
        return frame
    indexes = np.unique(np.linspace(0, len(frame) - 1, maximum).astype(int))
    return frame.iloc[indexes]


def persistence_from_position(position: np.ndarray) -> int:
    values = np.sign(np.asarray(position, dtype=float))
    if len(values) == 0:
        return 0
    nonflat = float(np.mean(values != 0))
    changes = np.r_[True, values[1:] != values[:-1]]
    starts = np.flatnonzero(changes)
    ends = np.r_[starts[1:], len(values)]
    runs = (ends - starts)[values[starts] != 0]
    switches = int(np.count_nonzero(values[1:] * values[:-1] < 0))
    days = len(values) / 1440.0
    return int(nonflat >= 0.90 and len(runs) > 0 and float(np.median(runs)) >= 1440 and switches / days <= 1)


def render_summary(row: dict, target: Path, strategy: str, timeframe: str, mode: str, dates: str) -> None:
    labels = ["Return (1x, %)", "Signed BE (bps)", "Sharpe", "Max DD (%)", "Persistent"]
    values = [row["Return"] * 100, row["Signed_BE"], row["Sharpe"], row["MaxDD"] * 100, row["Persistent"]]
    scale = np.array([[np.sign(value) if i < 4 else value] for i, value in enumerate(values)], dtype=float)
    fig, ax = plt.subplots(figsize=(5.2, 5.0))
    ax.imshow(scale, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
    ax.set_yticks(range(5), labels); ax.set_xticks([0], ["BTCUSDT"])
    for i, value in enumerate(values):
        text = f"{value:.2f}" if i < 4 else str(int(value))
        ax.text(0, i, text, ha="center", va="center", fontweight="bold")
    ax.set_title(f"{strategy} | {timeframe} | {mode}\n{dates}", fontsize=10)
    fig.tight_layout(); target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp.png"); fig.savefig(temporary, dpi=160, bbox_inches="tight")
    plt.close(fig); os.replace(temporary, target)


def normalized_path(path: Path, maker: bool) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if maker:
        return pd.DataFrame({
            "time": pd.to_datetime(frame.timestamp_ns, unit="ns", utc=True),
            "return": frame.cumulative_return_gross, "turnover": frame.cumulative_turnover,
            "position": frame.actual_position, "drawdown": frame.drawdown_gross,
        })
    return pd.DataFrame({
        "time": pd.to_datetime(frame.event_time_ns, unit="ns", utc=True),
        "return": frame.cumulative_return_with_premium, "turnover": frame.cumulative_turnover,
        "position": frame.executed_position, "drawdown": frame.drawdown,
    })


def render_performance(path: Path, target: Path, title: str, metrics: str, maker: bool) -> None:
    raw = normalized_path(path, maker)
    view = sample(raw)
    fig, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1, 1]})
    axes[0].plot(view.time, view["return"], lw=1.0, color="#1f77b4")
    turnover = axes[0].twinx(); turnover.plot(view.time, view.turnover, lw=.8, color="#f28e2b", alpha=.75)
    axes[0].set_ylabel("Return"); turnover.set_ylabel("Turnover (x)")
    axes[1].step(view.time, view.position, where="post", lw=.8, color="#4c78a8")
    axes[1].set_yticks([-1, 0, 1], ["SHORT", "FLAT", "LONG"]); axes[1].set_ylabel("Position")
    axes[2].fill_between(view.time, view.drawdown, 0, color="#e15759", alpha=.5)
    trough = int(raw.drawdown.to_numpy(float).argmin())
    axes[2].scatter(raw.time.iloc[trough], raw.drawdown.iloc[trough], color="black", s=24)
    axes[2].annotate(f"MaxDD {raw.drawdown.iloc[trough]:.2%}\n{raw.time.iloc[trough].date()}",
                     (raw.time.iloc[trough], raw.drawdown.iloc[trough]), xytext=(8, 8), textcoords="offset points")
    axes[2].set_ylabel("Drawdown"); axes[2].grid(alpha=.2)
    fig.suptitle(f"{title}\n{metrics}", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, .94)); target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp.png"); fig.savefig(temporary, dpi=155, bbox_inches="tight")
    plt.close(fig); os.replace(temporary, target)


def render_comparison(first_path: Path, maker_path: Path, first: pd.Series, maker: pd.Series, target: Path, title: str) -> None:
    left = pd.read_parquet(first_path)
    right = pd.read_parquet(maker_path)
    left_time = pd.to_datetime(left.timestamp_ns, unit="ns", utc=True)
    right_time = pd.to_datetime(right.timestamp_ns, unit="ns", utc=True)
    li = np.unique(np.linspace(0, len(left) - 1, min(len(left), 12000)).astype(int))
    ri = np.unique(np.linspace(0, len(right) - 1, min(len(right), 12000)).astype(int))
    fig, axes = plt.subplots(2, 1, figsize=(13, 7), gridspec_kw={"height_ratios": [2, 1]})
    axes[0].plot(left_time.iloc[li], left.cumulative_return.iloc[li], label="Same-window FIRST_TICK", lw=1)
    axes[0].plot(right_time.iloc[ri], right.cumulative_return_gross.iloc[ri], label="L1 BBO MAKER", lw=1)
    axes[0].set_ylabel("Cumulative Return"); axes[0].legend(); axes[0].grid(alpha=.2)
    metrics = ["Return", "Sharpe", "BE (bps)", "MaxDD", "Turnover"]
    first_values = [first.Return, first.Sharpe, first.Signed_BE_bps, first.Max_Drawdown, first.Turnover_raw]
    maker_values = [maker.Return_gross, maker.Sharpe_gross, maker.Signed_BE_bps_gross, maker.Max_Drawdown_gross, maker.Turnover_raw]
    axes[1].axis("off")
    table = axes[1].table(cellText=[[f"{v:.5f}" for v in first_values], [f"{v:.5f}" for v in maker_values]],
                          rowLabels=["FIRST_TICK", "MAKER"], colLabels=metrics, loc="center")
    table.auto_set_font_size(False); table.set_fontsize(9); table.scale(1, 1.5)
    fig.suptitle(title, fontsize=11); fig.tight_layout(rect=(0, 0, 1, .95))
    target.parent.mkdir(parents=True, exist_ok=True); temporary = target.with_suffix(".tmp.png")
    fig.savefig(temporary, dpi=155, bbox_inches="tight"); plt.close(fig); os.replace(temporary, target)


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--old-delivery", type=Path, required=True)
    parser.add_argument("--maker-root", type=Path, required=True)
    parser.add_argument("--acquisition-root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    args = parser.parse_args()
    selected = pd.read_csv(args.research_root / "selection/long_horizon_first_tick_selected_cases.csv")
    mappings = pd.concat([pd.read_csv(path) for path in sorted(args.maker_root.glob("case_mapping_shard_*.csv"))], ignore_index=True)
    metrics = pd.concat([pd.read_csv(path) for path in sorted(args.maker_root.glob("metrics_shard_*.csv"))], ignore_index=True)
    acquisition = pd.read_csv(args.acquisition_root / "acquisition_manifest.csv")
    if len(selected) != 160 or selected.strategy_id.nunique() != 160:
        raise ValueError("frozen selected universe changed")
    if mappings.case_key.nunique() != selected.semantic_group_id.nunique():
        raise ValueError("maker physical-case mapping incomplete")
    staging = args.delivery_root.with_name(args.delivery_root.name + ".staging")
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(args.research_root / "selection", staging / "selection", dirs_exist_ok=True)
    maker_metrics = metrics[metrics.execution_model.str.startswith("L1_BBO_MAKER")].set_index(["case_key", "variant"])
    first_metrics = metrics[metrics.execution_model.str.startswith("SAME_WINDOW_FIRST_TICK")].set_index(["case_key", "variant"])
    mapping_lookup = mappings.drop_duplicates("case_key").set_index("case_key")
    gating_rows, index_rows, wide_rows, mode_rows = [], [], [], []
    for row in selected.itertuples(index=False):
        strategy_root = staging / "strategies" / row.strategy_id
        key = case_key(str(row.semantic_group_id), str(row.timeframe))
        map_row = mapping_lookup.loc[key]
        shard = int(next(path.name.split("=")[1] for path in (args.maker_root / "paths").glob("shard=*") if (path / key).exists()))
        maker_path_root = args.maker_root / "paths" / f"shard={shard}" / key
        normal_summary_path = Path(str(row.normal_summary_path))
        normal_review = normal_summary_path.parent / "review_timeseries.parquet"
        normal_json = load_json(normal_summary_path)
        safe_group = str(row.semantic_group_id).replace(":", "__")
        reverse_root = args.research_root / "selected_reverse_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}" / f"semantic={safe_group}"
        reverse_json = load_json(reverse_root / "summary.json")
        reverse_review = reverse_root / "review_timeseries.parquet"
        normal_mode = {
            "strategy_id": row.strategy_id, "symbol": row.symbol, "timeframe": row.timeframe,
            "trading_mode": MODES[0], "evaluation_start": "2021-07-01", "evaluation_end": "2026-06-30",
            "calendar_days": 1826, "daily_observations": row.n_daily_observations,
            "Return": row.Return_FIRST_TICK, "Sharpe": row.Sharpe_FIRST_TICK,
            "Signed_BE": row.Signed_BE_FIRST_TICK, "MaxDD": row.MaxDD_FIRST_TICK,
            "Turnover": row.Turnover_FIRST_TICK, "Persistent": int(str(normal_json.get("persistence_structure_class", "")) == "DIRECTIONALLY_PERSISTENT"),
            "data_tier": "RAW_TRADES_FIRST_TICK", "status": "COMPLETED",
        }
        reverse_mode = {
            "strategy_id": row.strategy_id, "symbol": row.symbol, "timeframe": row.timeframe,
            "trading_mode": MODES[2], "evaluation_start": "2021-07-01", "evaluation_end": "2026-06-30",
            "calendar_days": 1826, "daily_observations": reverse_json["n_daily_observations"],
            "Return": reverse_json["Return_fee0"], "Sharpe": reverse_json["Sharpe"],
            "Signed_BE": reverse_json["BE_bps"], "MaxDD": reverse_json["MDD"],
            "Turnover": reverse_json["Turnover_raw"], "Persistent": int(str(reverse_json.get("persistence_structure_class", "")) == "DIRECTIONALLY_PERSISTENT"),
            "data_tier": "RAW_TRADES_FIRST_TICK", "status": "COMPLETED",
            "reverse_evidence_class": "LONG_HORIZON_EXPLORATORY_REVERSE",
        }
        mode_data = {MODES[0]: (normal_mode, normal_review, False), MODES[2]: (reverse_mode, reverse_review, False)}
        for variant, mode_name in (("NORMAL", MODES[1]), ("STRICT_REVERSE", MODES[3])):
            maker = maker_metrics.loc[(key, variant)]
            first = first_metrics.loc[(key, variant)]
            path = maker_path_root / f"{variant}__MAKER.parquet"
            actual = pd.read_parquet(path, columns=["actual_position"]).actual_position.to_numpy(float)
            maker_mode = {
                "strategy_id": row.strategy_id, "symbol": row.symbol, "timeframe": row.timeframe,
                "trading_mode": mode_name, "evaluation_start": MAKER_START.date().isoformat(),
                "evaluation_end": (MAKER_END - pd.Timedelta(days=1)).date().isoformat(),
                "calendar_days": int((MAKER_END - MAKER_START).days), "daily_observations": int((MAKER_END - MAKER_START).days),
                "Return": maker.Return_gross, "Sharpe": maker.Sharpe_gross,
                "Signed_BE": maker.Signed_BE_bps_gross, "MaxDD": maker.Max_Drawdown_gross,
                "Turnover": maker.Turnover_raw, "Persistent": persistence_from_position(actual),
                "data_tier": "PARTIAL_WINDOW_L1_BBO_MAKER", "status": "COMPLETED",
                "quantity_fill_ratio": maker.quantity_fill_ratio, "zero_fill_rate": maker.zero_fill_order_rate,
                "order_count": maker.submitted_orders, "fill_count": maker.OrderFilled_count,
                "partial_fill_count": maker.partial_fill_orders,
                "same_window_first_tick_Return": first.Return, "same_window_first_tick_Sharpe": first.Sharpe,
                "same_window_first_tick_BE": first.Signed_BE_bps, "same_window_first_tick_MaxDD": first.Max_Drawdown,
                "same_window_first_tick_Turnover": first.Turnover_raw,
                "reverse_evidence_class": "LONG_HORIZON_EXPLORATORY_REVERSE" if variant == "STRICT_REVERSE" else "",
            }
            mode_data[mode_name] = (maker_mode, path, True)
            comparison_target = strategy_root / mode_name / "execution_comparison.png"
            render_comparison(maker_path_root / f"{variant}__FIRST_TICK.parquet", path, first, maker,
                              comparison_target, f"{row.strategy_id} | {row.symbol} | {row.timeframe} | {variant}\nPARTIAL-LONG-HORIZON MAKER WINDOW {MAKER_START.date()} to {(MAKER_END-pd.Timedelta(days=1)).date()}")
        for mode_name, (mode_row, path, is_maker) in mode_data.items():
            mode_dir = strategy_root / mode_name
            atomic_csv(pd.DataFrame([mode_row]), mode_dir / "mode_summary.csv")
            dates = f"{mode_row['evaluation_start']} to {mode_row['evaluation_end']}"
            if is_maker: dates = "PARTIAL-LONG-HORIZON MAKER WINDOW | " + dates
            render_summary(mode_row, mode_dir / f"summary_{row.timeframe}.png", row.strategy_id, row.timeframe, mode_name, dates)
            metric_text = (f"Window={dates} | Return={mode_row['Return']:.2%} | Sharpe={mode_row['Sharpe']:.3f} | "
                           f"BE={mode_row['Signed_BE']:.3f} bps | MaxDD={mode_row['MaxDD']:.2%} | Turnover={mode_row['Turnover']:.2f}x")
            if is_maker:
                metric_text += f" | Fill={mode_row['quantity_fill_ratio']:.1%} | ZeroFill={mode_row['zero_fill_rate']:.1%}"
            perf = mode_dir / "performance" / row.timeframe / f"{row.symbol}__performance.png"
            render_performance(path, perf, f"{row.strategy_id} | {row.symbol} | {row.timeframe} | {mode_name}", metric_text, is_maker)
            mode_rows.append(mode_row)
        wide_rows.append({
            "strategy_id": row.strategy_id, "symbol": row.symbol, "timeframe": row.timeframe,
            "FIRST_TICK_NORMAL_Return": normal_mode["Return"], "FIRST_TICK_NORMAL_Sharpe": normal_mode["Sharpe"],
            "FIRST_TICK_NORMAL_BE": normal_mode["Signed_BE"], "FIRST_TICK_NORMAL_MaxDD": normal_mode["MaxDD"],
            "MAKER_NORMAL_Return": mode_data[MODES[1]][0]["Return"], "MAKER_NORMAL_Sharpe": mode_data[MODES[1]][0]["Sharpe"],
            "MAKER_NORMAL_BE": mode_data[MODES[1]][0]["Signed_BE"], "MAKER_NORMAL_MaxDD": mode_data[MODES[1]][0]["MaxDD"],
            "MAKER_NORMAL_fill_ratio": mode_data[MODES[1]][0]["quantity_fill_ratio"],
            "FIRST_TICK_REVERSE_Return": reverse_mode["Return"], "FIRST_TICK_REVERSE_Sharpe": reverse_mode["Sharpe"],
            "FIRST_TICK_REVERSE_BE": reverse_mode["Signed_BE"], "FIRST_TICK_REVERSE_MaxDD": reverse_mode["MaxDD"],
            "MAKER_REVERSE_Return": mode_data[MODES[3]][0]["Return"], "MAKER_REVERSE_Sharpe": mode_data[MODES[3]][0]["Sharpe"],
            "MAKER_REVERSE_BE": mode_data[MODES[3]][0]["Signed_BE"], "MAKER_REVERSE_MaxDD": mode_data[MODES[3]][0]["MaxDD"],
            "MAKER_REVERSE_fill_ratio": mode_data[MODES[3]][0]["quantity_fill_ratio"],
            "maker_start": MAKER_START.date().isoformat(), "maker_end": (MAKER_END-pd.Timedelta(days=1)).date().isoformat(),
            "reverse_evidence_class": "LONG_HORIZON_EXPLORATORY_REVERSE",
        })
        atomic_csv(pd.DataFrame([wide_rows[-1]]), strategy_root / "strategy_summary.csv")
        counts = {mode: len(list((strategy_root / mode).rglob("*.png"))) for mode in MODES}
        gating_rows.append({
            "strategy_id": row.strategy_id, "selected_by_NORMAL_FIRST_TICK": True,
            **{f"folder_{i+1:02d}_exists": (strategy_root / mode).is_dir() for i, mode in enumerate(MODES)},
            **{f"figures_{i+1:02d}": counts[mode] for i, mode in enumerate(MODES)},
            "complete_four_mode_output": all(counts[mode] > 0 for mode in MODES),
        })
        index_rows.append({
            "strategy_id": row.strategy_id, "source_origin": row.source_origin,
            "semantic_group_id": row.semantic_group_id, "selected_logical_cases": 1,
            "modes": ";".join(MODES), "maker_window": f"{MAKER_START.date()}/{(MAKER_END-pd.Timedelta(days=1)).date()}",
            "strategy_folder": f"strategies/{row.strategy_id}",
        })
    mode_frame = pd.DataFrame(mode_rows)
    gating = pd.DataFrame(gating_rows)
    atomic_csv(mode_frame, staging / "mode_comparison.csv")
    atomic_csv(gating, staging / "output_gating_audit.csv")
    atomic_csv(pd.DataFrame(index_rows), staging / "strategy_index.csv")
    atomic_csv(acquisition, staging / "data_provenance/maker_data_source.csv")
    coverage = pd.DataFrame([{
        "symbol": "BTCUSDT", "quote_source": "Binance official USD-M Futures historical bookTicker",
        "trade_source": "Binance official USD-M Futures historical trades", "data_tier": "L1_BBO_MAKER",
        "available_start": MAKER_START.date().isoformat(), "available_end": (MAKER_END-pd.Timedelta(days=1)).date().isoformat(),
        "calendar_days": int((MAKER_END-MAKER_START).days), "full_5y_coverage": False,
    }])
    atomic_csv(coverage, staging / "data_provenance/maker_data_coverage.csv")
    (staging / "data_provenance/data_source_note.md").write_text(
        "# Maker data source\n\nOfficial Binance USD-M Futures historical `bookTicker` was converted to L1 QuoteTick input; official raw trades were converted to TradeTick input. The maker interval is availability-frozen at 2023-05-16 through 2024-03-30 and is not represented as five-year coverage.\n",
        encoding="utf-8",
    )
    normal_maker = mode_frame[mode_frame.trading_mode.eq(MODES[1])]
    reverse_frame = mode_frame[mode_frame.trading_mode.eq(MODES[2])]
    maker_reverse = mode_frame[mode_frame.trading_mode.eq(MODES[3])]
    reverse_joined = reverse_frame.merge(
        selected[["strategy_id", "symbol", "timeframe", "Return_FIRST_TICK"]],
        on=["strategy_id", "symbol", "timeframe"], how="left", validate="one_to_one",
    )
    answers = pd.DataFrame([
        ["How many strategies were selected by NORMAL FIRST_TICK?", selected.strategy_id.nunique()],
        ["How many logical cases?", len(selected)],
        ["How many independent semantic groups?", selected.semantic_group_id.nunique()],
        ["How many strategies have all four actual performance modes?", int(gating.complete_four_mode_output.sum())],
        ["How many have partial maker-window modes?", selected.strategy_id.nunique()],
        ["How many have zero maker data?", 0],
        ["Median MAKER NORMAL Return delta vs same-window FIRST_TICK", float((normal_maker.Return-normal_maker.same_window_first_tick_Return).median())],
        ["Median MAKER NORMAL Sharpe delta vs same-window FIRST_TICK", float((normal_maker.Sharpe-normal_maker.same_window_first_tick_Sharpe).median())],
        ["How many MAKER_NORMAL become worse by Return?", int((normal_maker.Return < normal_maker.same_window_first_tick_Return).sum())],
        ["How many MAKER_NORMAL improve by Return?", int((normal_maker.Return > normal_maker.same_window_first_tick_Return).sum())],
        ["How many REVERSE improve historical Return?", int((reverse_joined.Return > reverse_joined.Return_FIRST_TICK).sum())],
        ["How many MAKER_REVERSE remain positive?", int((maker_reverse.Return.gt(0)&maker_reverse.Sharpe.gt(0)&maker_reverse.Signed_BE.gt(0)).sum())],
        ["Clean forward validated reverse cases", 0],
    ], columns=["question", "answer"])
    atomic_csv(answers, staging / "key_answers.csv")
    validation = {
        "status": "PASSED", "selected_strategy_ids": int(selected.strategy_id.nunique()),
        "selected_logical_cases": len(selected), "selected_semantic_groups": int(selected.semantic_group_id.nunique()),
        "performance_figures_per_mode": {mode: len(selected) for mode in MODES},
        "complete_four_mode_strategies": int(gating.complete_four_mode_output.sum()),
        "partial_window_maker_strategies": int(selected.strategy_id.nunique()),
        "zero_maker_data_strategies": 0, "empty_unexplained_mode_folders": 0,
        "maker_full_5y_coverage": False, "maker_window": f"[{MAKER_START.date()},{MAKER_END.date()})",
        "clean_forward_validated_reverse": 0, "live_trading_used": False,
    }
    atomic_json(validation, staging / "validation_summary.json")
    if not gating.complete_four_mode_output.all():
        raise ValueError("one or more selected strategies lacks four-mode output")
    args.old_delivery.joinpath("SUPERSEDED_INCOMPLETE_MODE_OUTPUT.txt").write_text(
        "Superseded by strategy_four_mode_review: maker/reverse performance may not gate output.\n", encoding="utf-8"
    )
    if args.delivery_root.exists(): shutil.rmtree(args.delivery_root)
    os.replace(staging, args.delivery_root)
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
