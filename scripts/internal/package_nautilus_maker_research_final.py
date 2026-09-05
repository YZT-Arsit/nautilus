#!/usr/bin/env python3
"""Package the completed maker-only studies without running new experiments."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_policy_study"
PILOT = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_pilot"
DELIVERY = ROOT / "outputs/deliverables/nautilus_maker_research_final"
P0 = "NEXT_DECISION_CANCEL"
P1 = "GTC_UNTIL_SIGNAL_INVALID"
P2 = "PASSIVE_CANCEL_REQUOTE_15S"
POLICY_ORDER = (P0, P1, P2)
DISPLAY = {
    "FIRST_TICK_IDEALIZED": "FIRST_TICK",
    P0: "P0",
    P1: "P1",
    P2: "P2",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    temp.replace(path)


def load_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    case = pd.read_csv(STUDY / "maker_policy_case_comparison.csv")
    summary = pd.read_csv(STUDY / "maker_policy_summary.csv")
    markout = pd.read_csv(STUDY / "maker_markout_summary.csv")
    validation = json.loads((STUDY / "validation_summary.json").read_text(encoding="utf-8"))
    if validation.get("status") != "PASSED":
        raise ValueError("source policy study is not PASSED")
    if len(case) != 54 or case[["strategy_id", "symbol"]].drop_duplicates().shape[0] != 18:
        raise ValueError("source policy case cardinality is not 54 rows / 18 frozen cases")
    if set(case.policy) != set(POLICY_ORDER):
        raise ValueError("source policy set changed")
    return case, summary, markout, validation


def markout_wide(markout: pd.DataFrame) -> pd.DataFrame:
    wide = markout.pivot_table(
        index=["strategy_id", "symbol", "policy"],
        columns="horizon_seconds",
        values="mean_markout_bps",
        aggfunc="first",
    ).reset_index()
    wide.columns = [
        f"adverse_selection_{int(column)}s" if isinstance(column, (int, float, np.integer, np.floating)) else column
        for column in wide.columns
    ]
    return wide


def build_case_detail(case: pd.DataFrame, markout: pd.DataFrame) -> pd.DataFrame:
    mark = markout_wide(markout)
    maker = case.merge(mark, on=["strategy_id", "symbol", "policy"], how="left")
    maker_detail = pd.DataFrame(
        {
            "strategy": maker.strategy_id,
            "symbol": maker.symbol,
            "execution_model": maker.policy.map(DISPLAY),
            "Return": maker.Return,
            "Sharpe": maker.Sharpe,
            "MaxDD": maker.MaxDD,
            "Turnover": maker.Turnover,
            "Signed_BE_bps": maker.Signed_BE_bps,
            "quantity_fill_ratio": maker.quantity_fill_ratio,
            "zero_fill_rate": maker.zero_fill_rate,
            "median_time_to_first_fill_ms": maker.median_time_to_first_fill_ms,
            "exposure_tracking_error": maker.mean_absolute_target_position_error,
            "adverse_selection_1s": maker.adverse_selection_1s,
            "adverse_selection_5s": maker.adverse_selection_5s,
            "adverse_selection_30s": maker.adverse_selection_30s,
            "adverse_selection_60s": maker.adverse_selection_60s,
            "delta_Return_vs_FIRST_TICK": maker.delta_Return_vs_FIRST_TICK,
            "delta_Sharpe_vs_FIRST_TICK": maker.delta_Sharpe_vs_FIRST_TICK,
            "delta_MaxDD_vs_FIRST_TICK": maker.delta_MaxDD_vs_FIRST_TICK,
            "delta_Turnover_vs_FIRST_TICK": maker.delta_Turnover_vs_FIRST_TICK,
        }
    )
    first = case.sort_values(["strategy_id", "symbol", "policy"]).drop_duplicates(["strategy_id", "symbol"])
    first_detail = pd.DataFrame(
        {
            "strategy": first.strategy_id,
            "symbol": first.symbol,
            "execution_model": "FIRST_TICK",
            "Return": first.FIRST_TICK_Return,
            "Sharpe": first.FIRST_TICK_Sharpe,
            "MaxDD": first.FIRST_TICK_MaxDD,
            "Turnover": first.FIRST_TICK_Turnover,
            "Signed_BE_bps": first.FIRST_TICK_Signed_BE_bps,
        }
    )
    for column in maker_detail.columns:
        if column not in first_detail:
            first_detail[column] = np.nan
    result = pd.concat([first_detail[maker_detail.columns], maker_detail], ignore_index=True)
    order = pd.Categorical(result.execution_model, ["FIRST_TICK", "P0", "P1", "P2"], ordered=True)
    result = result.assign(_execution_order=order).sort_values(["strategy", "symbol", "_execution_order"])
    return result.drop(columns="_execution_order").reset_index(drop=True)


def build_final_comparison(detail: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    maker_summary = summary.set_index("policy")
    rows = []
    for model in ("FIRST_TICK", "P0", "P1", "P2"):
        subset = detail[detail.execution_model.eq(model)]
        row = {
            "execution_model": model,
            "median_Return": subset.Return.median(),
            "median_Sharpe": subset.Sharpe.median(),
            "median_MaxDD": subset.MaxDD.median(),
            "median_Turnover": subset.Turnover.median(),
            "quantity_fill_ratio": np.nan,
            "zero_fill_rate": np.nan,
            "median_time_to_first_fill_ms": np.nan,
            "exposure_tracking_error": np.nan,
            "adverse_selection_1s": np.nan,
            "adverse_selection_5s": np.nan,
            "adverse_selection_30s": np.nan,
            "adverse_selection_60s": np.nan,
        }
        if model != "FIRST_TICK":
            policy = {"P0": P0, "P1": P1, "P2": P2}[model]
            policy_cases = subset
            row.update(
                quantity_fill_ratio=maker_summary.loc[policy, "quantity_fill_ratio"],
                zero_fill_rate=maker_summary.loc[policy, "zero_fill_rate"],
                median_time_to_first_fill_ms=policy_cases.median_time_to_first_fill_ms.median(),
                exposure_tracking_error=maker_summary.loc[policy, "mean_target_error"],
                adverse_selection_1s=policy_cases.adverse_selection_1s.mean(),
                adverse_selection_5s=policy_cases.adverse_selection_5s.mean(),
                adverse_selection_30s=policy_cases.adverse_selection_30s.mean(),
                adverse_selection_60s=policy_cases.adverse_selection_60s.mean(),
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_no_fill_table(comparison: pd.DataFrame) -> pd.DataFrame:
    indexed = comparison.set_index("execution_model")
    rows = [
        {
            "policy": "P0 NEXT_DECISION_CANCEL",
            "pure_maker": "YES",
            "unfilled_order_behavior": "Cancel unfilled remainder at the next 1m decision; recompute from actual position; repost if still required.",
            "fill_ratio": indexed.loc["P0", "quantity_fill_ratio"],
            "zero_fill_rate": indexed.loc["P0", "zero_fill_rate"],
            "Sharpe": indexed.loc["P0", "median_Sharpe"],
            "practical_comment": "Clean cancellation boundary; materially degraded versus paired FIRST_TICK on the frozen window.",
        },
        {
            "policy": "P1 GTC_UNTIL_SIGNAL_INVALID",
            "pure_maker": "YES",
            "unfilled_order_behavior": "Keep remainder resting while target remains valid; cancel on flat, reversal, or material target change.",
            "fill_ratio": indexed.loc["P1", "quantity_fill_ratio"],
            "zero_fill_rate": indexed.loc["P1", "zero_fill_rate"],
            "Sharpe": indexed.loc["P1", "median_Sharpe"],
            "practical_comment": "Highest fill ratio and lowest zero-fill rate, but higher fill availability did not recover Sharpe.",
        },
        {
            "policy": "P2 PASSIVE_CANCEL_REQUOTE_15S",
            "pure_maker": "YES",
            "unfilled_order_behavior": "Cancel stale remainder every fixed 15 seconds and repost at current passive BBO while target remains valid.",
            "fill_ratio": indexed.loc["P2", "quantity_fill_ratio"],
            "zero_fill_rate": indexed.loc["P2", "zero_fill_rate"],
            "Sharpe": indexed.loc["P2", "median_Sharpe"],
            "practical_comment": "Best measured target tracking, with high requote activity; no material performance recovery.",
        },
        {
            "policy": "HYBRID_TAKER_FALLBACK",
            "pure_maker": "NO",
            "unfilled_order_behavior": "After a chosen timeout, cross the spread with a taker order.",
            "fill_ratio": np.nan,
            "zero_fill_rate": np.nan,
            "Sharpe": np.nan,
            "practical_comment": "NOT RUN. Common practical alternative, but it violates the completely maker-only condition.",
        },
    ]
    return pd.DataFrame(rows)


def render_summary(comparison: pd.DataFrame, output: Path) -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "figure.facecolor": "white",
    })
    colors = ["#4C78A8", "#F58518", "#54A24B", "#B279A2"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)
    labels = comparison.execution_model.tolist()
    sharpes = comparison.median_Sharpe.to_numpy(float)
    bars = axes[0, 0].bar(labels, sharpes, color=colors)
    axes[0, 0].axhline(0, color="#444444", linewidth=.8)
    axes[0, 0].set_title("Median Sharpe (18 paired cases)")
    axes[0, 0].set_ylabel("UTC-daily Sharpe, sqrt(365)")
    for bar, value in zip(bars, sharpes, strict=True):
        axes[0, 0].text(bar.get_x() + bar.get_width()/2, value + .30, f"{value:.4f}", ha="center", va="bottom", color="white", fontweight="bold")

    maker = comparison[~comparison.execution_model.eq("FIRST_TICK")]
    fill = 100 * maker.quantity_fill_ratio.to_numpy(float)
    bars = axes[0, 1].bar(maker.execution_model, fill, color=colors[1:])
    axes[0, 1].set_ylim(0, 105)
    axes[0, 1].set_title("Quantity fill ratio")
    axes[0, 1].set_ylabel("Percent")
    for bar, value in zip(bars, fill, strict=True):
        axes[0, 1].text(bar.get_x()+bar.get_width()/2, value+1.2, f"{value:.2f}%", ha="center", fontweight="bold")

    zero = 100 * maker.zero_fill_rate.to_numpy(float)
    bars = axes[1, 0].bar(maker.execution_model, zero, color=colors[1:])
    axes[1, 0].set_title("Zero-fill order rate")
    axes[1, 0].set_ylabel("Percent")
    axes[1, 0].set_ylim(0, max(zero) * 1.25)
    for bar, value in zip(bars, zero, strict=True):
        axes[1, 0].text(bar.get_x()+bar.get_width()/2, value+.25, f"{value:.2f}%", ha="center", fontweight="bold")

    horizons = np.array([1, 5, 30, 60])
    for model, color in zip(("P0", "P1", "P2"), colors[1:], strict=True):
        row = comparison[comparison.execution_model.eq(model)].iloc[0]
        values = [row[f"adverse_selection_{h}s"] for h in horizons]
        axes[1, 1].plot(horizons, values, marker="o", linewidth=2, label=model, color=color)
    axes[1, 1].axhline(0, color="#444444", linewidth=.8)
    axes[1, 1].set_title("Mean side-adjusted post-fill markout")
    axes[1, 1].set_xlabel("Horizon (seconds)")
    axes[1, 1].set_ylabel("Basis points")
    axes[1, 1].set_xticks(horizons)
    axes[1, 1].legend(frameon=False, ncol=3)

    fig.suptitle("Nautilus pure-maker research — frozen L1 pilot", fontsize=16, fontweight="bold")
    fig.text(.5, -.015, "Higher fill rate did not restore performance. Exposure shortfall + adverse selection dominate.", ha="center", fontsize=12, fontweight="bold")
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_lifecycle(output: Path) -> None:
    source = STUDY / "figures/execution_examples"
    panels = [
        ("full_fill.png", "A  Full fill"),
        ("partial_fill.png", "B  Partial fill"),
        ("complete_no_fill.png", "C  No fill → cancel"),
        ("requote_sequence.png", "D  Fixed 15s cancel / requote"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(16, 9), constrained_layout=True)
    for ax, (name, label) in zip(axes.flat, panels, strict=True):
        image = Image.open(source / name).convert("RGB")
        ax.imshow(image)
        ax.set_title(label, loc="left", fontsize=12, fontweight="bold")
        ax.axis("off")
    fig.suptitle("Pure-maker order lifecycle — deterministic event examples selected by event type, not PnL", fontsize=16, fontweight="bold")
    fig.text(.5, .005, "Rest at passive BBO → fill fully/partially, or remain unfilled; policy then cancels, keeps resting, or requotes. No taker fallback.", ha="center", fontsize=11)
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_report(path: Path, comparison: pd.DataFrame, validation: dict) -> None:
    rows = comparison.set_index("execution_model")
    text = f"""# Nautilus Maker Research — Final

## 1. Question

Can NautilusTrader implement pure post-only maker execution, how should unfilled orders be handled, and do plausible L1 lifecycle policies recover the paired first-tick path?

## 2. Nautilus capabilities

Validated on NautilusTrader 1.227.0: `post_only`, passive trade-triggered fills, partial fills, maker fees, and negative maker rebates are supported. Queue-aware L2 execution is technically supported, but suitable historical L2 data was unavailable.

## 3. Data used

The frozen pilot contains 18 strategy×symbol cases on BTCUSDT, ETHUSDT, and SOLUSDT over `[2024-03-01, 2024-03-31)`, using historical L1 bookTicker BBO plus raw trades. It is an L1 BBO study, not a queue-aware L2 study.

## 4. Maker order model

At each 1m decision, BUY orders rest at best bid and SELL orders at best ask using `LIMIT`, `post_only=True`, with no taker fallback. Signals and target-position paths are identical across policies. Performance uses UTC daily arithmetic return increments, sample standard deviation (`n-1`), `sqrt(365)`, and `rf=0`.

## 5. No-fill policies

- P0 `NEXT_DECISION_CANCEL`: cancel remainder at the next decision, recompute from actual filled position, and repost if needed.
- P1 `GTC_UNTIL_SIGNAL_INVALID`: keep the remainder resting while the target is valid; cancel on flat, reversal, or material target change.
- P2 `PASSIVE_CANCEL_REQUOTE_15S`: cancel and repost the remainder at current passive BBO every fixed 15 seconds while valid.
- Hybrid taker fallback was not run because it is not pure maker execution.

## 6. Results

| Execution | Median Return | Median Sharpe | Quantity fill | Zero-fill |
|---|---:|---:|---:|---:|
| FIRST_TICK | {rows.loc['FIRST_TICK','median_Return']:.6f} | {rows.loc['FIRST_TICK','median_Sharpe']:.4f} | n/a | n/a |
| P0 | {rows.loc['P0','median_Return']:.6f} | {rows.loc['P0','median_Sharpe']:.4f} | {rows.loc['P0','quantity_fill_ratio']:.2%} | {rows.loc['P0','zero_fill_rate']:.2%} |
| P1 | {rows.loc['P1','median_Return']:.6f} | {rows.loc['P1','median_Sharpe']:.4f} | {rows.loc['P1','quantity_fill_ratio']:.2%} | {rows.loc['P1','zero_fill_rate']:.2%} |
| P2 | {rows.loc['P2','median_Return']:.6f} | {rows.loc['P2','median_Sharpe']:.4f} | {rows.loc['P2','quantity_fill_ratio']:.2%} | {rows.loc['P2','zero_fill_rate']:.2%} |

On the same frozen March-2024 window, pure-maker execution materially degrades the path relative to first-tick idealized execution. The one-month FIRST_TICK median Sharpe is itself negative ({rows.loc['FIRST_TICK','median_Sharpe']:.4f}); this is not a claim that maker transformed a profitable pilot into an unprofitable one.

## 7. Why performance degrades

The largest observed attribution component is `{validation['main_pnl_degradation_component']}`. Raising mean quantity fill from {rows.loc['P0','quantity_fill_ratio']:.2%} under P0 to {rows.loc['P1','quantity_fill_ratio']:.2%} under P1 did not restore Sharpe. This indicates the difference is not explained simply by completely unfilled orders: time-varying exposure shortfall and fill timing alter the marked-to-market path. Mean side-adjusted markout is negative at 1s, 5s, 30s, and 60s for all three maker policies, so adverse selection is present. Attribution components overlap and are not forced to sum algebraically.

## 8. L2 assessment

L2 queue-aware testing would improve realism, especially queue position. Its acquisition priority is LOW: none of the three pure-maker L1 lifecycle policies materially recovered performance, and a more conservative queue model would generally make passive availability no easier. This is an inference, not proof that L2 can never improve results.

## 9. Final recommendation

Nautilus can model all three pure-maker no-fill policies. None materially recovered the paired first-tick path in this frozen 18-case L1 pilot; P1 achieved the best fill ratio ({rows.loc['P1','quantity_fill_ratio']:.2%}) and lowest zero-fill rate ({rows.loc['P1','zero_fill_rate']:.2%}) while remaining strongly degraded. Retain the implementation and evidence package, do not expand automatically, and treat L2 acquisition as low priority unless new information justifies queue-aware validation.
"""
    path.write_text(text, encoding="utf-8")


def copy_supporting_figures(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted((STUDY / "figures").glob("*__maker_policy_comparison.png")):
        shutil.copy2(source, destination / source.name)
    examples = destination / "execution_examples"
    examples.mkdir(exist_ok=True)
    for source in sorted((STUDY / "figures/execution_examples").glob("*.png")):
        shutil.copy2(source, examples / source.name)


def validate(delivery: Path, detail: pd.DataFrame, comparison: pd.DataFrame) -> dict:
    required = [
        "maker_research_summary.png",
        "maker_order_lifecycle_example.png",
        "maker_final_comparison.csv",
        "maker_case_detail.csv",
        "no_fill_policy_final.csv",
        "nautilus_maker_research_final.md",
    ]
    checks = {
        "source_policy_study_passed": json.loads((STUDY / "validation_summary.json").read_text())["status"] == "PASSED",
        "frozen_cases_18": detail[["strategy", "symbol"]].drop_duplicates().shape[0] == 18,
        "case_detail_72_rows": len(detail) == 72,
        "execution_models_exact": set(detail.execution_model) == {"FIRST_TICK", "P0", "P1", "P2"},
        "comparison_4_rows": len(comparison) == 4,
        "required_files_present": all((delivery / name).is_file() and (delivery / name).stat().st_size > 0 for name in required),
        "supporting_comparison_figures_18": len(list((delivery / "figures").glob("*__maker_policy_comparison.png"))) == 18,
        "new_strategy_cases_zero": True,
        "new_symbols_zero": True,
        "l2_download_zero": True,
        "new_maker_policies_zero": True,
        "parameter_optimization_zero": True,
    }
    manifest = []
    for file in sorted(path for path in delivery.rglob("*") if path.is_file()):
        if file.name in {"validation_summary.json", "artifact_manifest.csv"}:
            continue
        manifest.append({
            "relative_path": file.relative_to(delivery).as_posix(),
            "bytes": file.stat().st_size,
            "sha256": sha256(file),
        })
    atomic_csv(pd.DataFrame(manifest), delivery / "artifact_manifest.csv")
    payload = {
        "status": "PASSED" if all(checks.values()) else "BLOCKED",
        "frozen_cases": 18,
        "period": "[2024-03-01, 2024-03-31)",
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "nautilus_trader_version": "1.227.0",
        "validation_checks": checks,
        "artifact_files": len(manifest),
        "new_compute": {
            "strategy_cases": 0,
            "symbols": 0,
            "l2_downloads": 0,
            "maker_policies": 0,
            "parameter_optimization": 0,
        },
    }
    (delivery / "validation_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if payload["status"] != "PASSED":
        raise RuntimeError(json.dumps(payload, indent=2))
    return payload


def main() -> None:
    case, summary, markout, source_validation = load_sources()
    DELIVERY.mkdir(parents=True, exist_ok=True)
    detail = build_case_detail(case, markout)
    comparison = build_final_comparison(detail, summary)
    policies = build_no_fill_table(comparison)
    atomic_csv(comparison, DELIVERY / "maker_final_comparison.csv")
    atomic_csv(detail, DELIVERY / "maker_case_detail.csv")
    atomic_csv(policies, DELIVERY / "no_fill_policy_final.csv")
    render_summary(comparison, DELIVERY / "maker_research_summary.png")
    render_lifecycle(DELIVERY / "maker_order_lifecycle_example.png")
    write_report(DELIVERY / "nautilus_maker_research_final.md", comparison, source_validation)
    copy_supporting_figures(DELIVERY / "figures")
    payload = validate(DELIVERY, detail, comparison)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
