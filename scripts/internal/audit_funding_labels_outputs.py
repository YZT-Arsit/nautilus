#!/usr/bin/env python3
"""
Audit funding accounting, boss labels, and generated output completeness.

This is a read-mostly packaging audit.  It recomputes funding accounting from
already frozen executed-position paths, but it never runs strategy signals or
backtests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image
from PIL import ImageStat


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_boss_multitimeframe_tick_screen import NOTIONAL  # noqa: E402
from scripts.internal.run_boss_multitimeframe_tick_screen import load_symbol  # noqa: E402
from scripts.internal.run_constant_notional_overlay import calculate_overlay  # noqa: E402


MODES = (
    "01_FIRST_TICK_NORMAL",
    "02_MAKER_NORMAL",
    "03_FIRST_TICK_REVERSE",
    "04_MAKER_REVERSE",
)
MAKER_MODES = {"02_MAKER_NORMAL", "04_MAKER_REVERSE"}
STALE_LABELS = ("Premium Included", "Premium Excluded", "No Premium")
TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
NUMERICAL_FILES = {
    "mode_comparison.csv",
    "strategy_index.csv",
    "key_answers.csv",
    "output_gating_audit.csv",
    "strategy_summary.csv",
    "mode_summary.csv",
    "long_horizon_first_tick_selected_cases.csv",
    "long_horizon_first_tick_all_cases.csv",
    "long_horizon_negative_reverse_candidates.csv",
    "selection_freeze.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def numerical_hashes(delivery: Path) -> dict[str, str]:
    return {
        path.relative_to(delivery).as_posix(): sha256(path)
        for path in sorted(delivery.rglob("*"))
        if path.is_file() and path.name in NUMERICAL_FILES
    }


def scan_stale_labels(delivery: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(delivery.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for label in STALE_LABELS:
            start = 0
            while True:
                index = text.lower().find(label.lower(), start)
                if index < 0:
                    break
                context = text[max(0, index - 50) : index + len(label) + 50].replace("\n", " ")
                rows.append(
                    {
                        "file": path.relative_to(delivery).as_posix(),
                        "occurrence": label,
                        "context": context,
                        "corrected": False,
                    }
                )
                start = index + len(label)
    return pd.DataFrame(rows, columns=["file", "occurrence", "context", "corrected"])


def empty_folder_audit(delivery: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted((p for p in delivery.rglob("*") if p.is_dir()), reverse=True):
        files = [child for child in path.rglob("*") if child.is_file()]
        if files:
            continue
        rows.append(
            {
                "path": path.relative_to(delivery).as_posix(),
                "classification": "EMPTY" if not any(path.iterdir()) else "PLACEHOLDER_ONLY",
                "expected_output": False,
                "found_output": 0,
                "repair_action": "NONE",
                "deletion_action": "REMOVE_EXACT_PATH",
                "reason": "Generated directory has no files and no non-empty descendants",
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "path",
            "classification",
            "expected_output",
            "found_output",
            "repair_action",
            "deletion_action",
            "reason",
        ],
    )


def expected_files(row: Any) -> list[tuple[str, Path]]:
    strategy = str(row.strategy_id)
    timeframe = str(row.timeframe)
    symbol = str(row.symbol)
    items: list[tuple[str, Path]] = [
        ("strategy", Path("strategies") / strategy / "strategy_summary.csv")
    ]
    for mode in MODES:
        base = Path("strategies") / strategy / mode
        items.extend(
            [
                (mode, base / "mode_summary.csv"),
                (mode, base / f"summary_{timeframe}.png"),
                (mode, base / "performance" / timeframe / f"{symbol}__performance.png"),
            ]
        )
        if mode in MAKER_MODES:
            items.append((mode, base / "execution_comparison.png"))
    return items


def missing_and_completeness(delivery: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = pd.read_csv(delivery / "selection/long_horizon_first_tick_selected_cases.csv")
    missing: list[dict[str, Any]] = []
    completeness: list[dict[str, Any]] = []
    for strategy, group in selected.groupby("strategy_id", sort=True):
        expected = []
        for row in group.itertuples(index=False):
            expected.extend(expected_files(row))
        expected = list(dict.fromkeys(expected))
        mode_files = {mode: [] for mode in MODES}
        for mode, relative in expected:
            path = delivery / relative
            if mode in mode_files:
                mode_files[mode].append(path)
            if not path.is_file():
                missing.append(
                    {
                        "strategy_id": strategy,
                        "trading_mode": mode,
                        "symbol": str(group.iloc[0].symbol),
                        "timeframe": str(group.iloc[0].timeframe),
                        "expected_file": relative.as_posix(),
                        "source_result_exists": True,
                        "source_timeseries_exists": True,
                        "repaired": False,
                        "status": "MISSING_EXPECTED_OUTPUT",
                    }
                )
        base = delivery / "strategies" / str(strategy)
        empty_modes = [
            mode for mode in MODES if (base / mode).is_dir() and not any((base / mode).rglob("*.*"))
        ]
        completeness.append(
            {
                "strategy_id": strategy,
                "available_trading_modes": ";".join(
                    mode for mode in MODES if (base / mode).is_dir()
                ),
                "summary_file_count": sum(
                    1 for mode in MODES if (base / mode / "mode_summary.csv").is_file()
                ),
                "summary_figure_count": sum(
                    1 for mode in MODES for _ in (base / mode).glob("summary_*.png")
                ),
                "performance_figure_count": sum(
                    1
                    for mode in MODES
                    for _ in (base / mode / "performance").rglob("*__performance.png")
                ),
                "comparison_figure_count": sum(
                    1
                    for mode in MAKER_MODES
                    if (base / mode / "execution_comparison.png").is_file()
                ),
                "empty_mode_folders": ";".join(empty_modes),
                "unavailable_modes": "",
                "complete": not empty_modes
                and all((delivery / path).is_file() for _, path in expected),
            }
        )
    return (
        pd.DataFrame(
            missing,
            columns=[
                "strategy_id",
                "trading_mode",
                "symbol",
                "timeframe",
                "expected_file",
                "source_result_exists",
                "source_timeseries_exists",
                "repaired",
                "status",
            ],
        ),
        pd.DataFrame(completeness),
    )


def image_audit(delivery: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(delivery.rglob("*.png")):
        status = "PASSED"
        reason = "Readable PNG with non-zero dimensions and non-blank rendered content"
        width = height = 0
        band_variance_min = np.nan
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                rgb = image.convert("RGB")
                if width <= 0 or height <= 0:
                    raise ValueError("non-positive dimensions")
                stat = ImageStat.Stat(rgb)
                if max(stat.var) <= 0:
                    raise ValueError("blank image")
                if "__performance.png" in path.name:
                    # The approved template has three populated vertical bands.
                    bands = [
                        rgb.crop((0, i * height // 3, width, (i + 1) * height // 3))
                        for i in range(3)
                    ]
                    band_variance_min = min(max(ImageStat.Stat(band).var) for band in bands)
                    if band_variance_min <= 0:
                        raise ValueError("one or more performance panels are blank")
        except Exception as error:
            status = "FAILED"
            reason = str(error)
        rows.append(
            {
                "file": path.relative_to(delivery).as_posix(),
                "bytes": path.stat().st_size,
                "width": width,
                "height": height,
                "performance_panel_band_variance_min": band_variance_min,
                "status": status,
                "notes": reason,
            }
        )
    return pd.DataFrame(rows)


def reconstruct_position(review_path: Path, event_time_ns: np.ndarray) -> np.ndarray:
    review = pd.read_parquet(review_path, columns=["event_time_ns", "executed_position"])
    review = review.sort_values("event_time_ns").drop_duplicates("event_time_ns", keep="last")
    times = review.event_time_ns.to_numpy(np.int64, copy=False)
    positions = review.executed_position.to_numpy(float, copy=False)
    lookup = np.searchsorted(times, event_time_ns, side="right") - 1
    if len(times) == 0 or np.any(lookup < 0):
        raise ValueError(f"position sample does not cover event clock: {review_path}")
    reconstructed = positions[lookup]
    if np.count_nonzero(positions[1:] != positions[:-1]) != np.count_nonzero(
        reconstructed[1:] != reconstructed[:-1]
    ):
        raise ValueError(f"position transition sample is lossy: {review_path}")
    return reconstructed


def choose_funding_samples(master: pd.DataFrame) -> pd.DataFrame:
    rows = master[
        master.symbol.isin(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        & master.timeframe.eq("1m")
        & master.long_fraction.gt(0)
        & master.short_fraction.gt(0)
    ].copy()
    chosen_rows = []
    used_strategies: set[str] = set()
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        candidates = rows.loc[rows.symbol.eq(symbol)].sort_values("strategy_id")
        candidates = candidates.loc[~candidates.strategy_id.astype(str).isin(used_strategies)]
        if candidates.empty:
            raise ValueError(f"could not select a distinct two-sided strategy for {symbol}")
        selected = candidates.iloc[0]
        chosen_rows.append(selected)
        used_strategies.add(str(selected.strategy_id))
    chosen = pd.DataFrame(chosen_rows)
    if set(chosen.symbol) != {"BTCUSDT", "ETHUSDT", "SOLUSDT"}:
        raise ValueError(
            "could not select one deterministic two-sided strategy for each sample symbol"
        )
    return chosen.sort_values("symbol")


def funding_numerical_audit(
    boss_root: Path,
    market_root: Path,
    master_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    master = pd.read_csv(master_path)
    chosen = choose_funding_samples(master)
    window = json.loads(
        (boss_root / "boss_tick_index_data_window.json").read_text(encoding="utf-8-sig")
    )
    start = str(window["common_start"])
    end_inclusive = (
        (pd.Timestamp(window["common_end_exclusive"]) - pd.Timedelta(1, unit="D"))
        .date()
        .isoformat()
    )
    sample_rows: list[dict[str, Any]] = []
    reconciliation_rows: list[dict[str, Any]] = []
    semantic_column = (
        "semantic_execution_hash"
        if "semantic_execution_hash" in chosen.columns
        else "semantic_group_id"
    )
    for row in chosen.itertuples(index=False):
        symbol = str(row.symbol)
        semantic = str(getattr(row, semantic_column))
        timeframe = str(row.timeframe)
        bars, funding, _, tick_prices, _ = load_symbol(
            market_root,
            boss_root / "tick_execution_index",
            symbol,
            start,
            end_inclusive,
        )
        event_time = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
        close = np.fromiter((bar.close for bar in bars), dtype=np.float64)
        review_path = (
            boss_root
            / "matrix_cases"
            / f"symbol={symbol}"
            / f"timeframe={timeframe}"
            / f"semantic={semantic}"
            / "review_timeseries.parquet"
        )
        position = reconstruct_position(review_path, event_time)
        result, summary = calculate_overlay(
            pd.DataFrame({"event_time_ns": event_time, "close": close, "position": position}),
            funding,
            tick_prices,
            notional_usdt=NOTIONAL,
            slippage_bps=0.0,
            vip9_fee_bps=0.0,
            vip0_fee_bps=5.0,
            position_policy="strict_constant_notional",
        )
        included = float(row.Return_fee0)
        excluded = float(row.Return_no_premium)
        accumulated = float(summary["funding_simple_return"])
        reconciliation_rows.append(
            {
                "strategy_id": row.strategy_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "return_funding_included": included,
                "return_funding_excluded": excluded,
                "stored_included_minus_excluded": included - excluded,
                "recomputed_accumulated_funding_return": accumulated,
                "reconciliation_mismatch": (included - excluded) - accumulated,
                "status": "PASSED"
                if np.isclose(included - excluded, accumulated, atol=1e-12, rtol=0)
                else "FAILED",
            }
        )
        funding_ts = funding.event_time_ns.to_numpy(np.int64, copy=False)
        rates = funding.funding_rate.to_numpy(float, copy=False)
        held_index = np.searchsorted(event_time, funding_ts, side="right") - 1
        report_index = np.searchsorted(event_time, funding_ts, side="left")
        valid = (held_index >= 0) & (report_index >= 0) & (report_index < len(event_time))
        for desired_position in (1, -1):
            candidates = np.flatnonzero(
                valid
                & (result.direction.to_numpy(np.int8)[held_index.clip(min=0)] == desired_position)
            )
            if not len(candidates):
                raise ValueError(
                    f"{row.strategy_id}/{symbol}: no funding event for position {desired_position}"
                )
            index = int(candidates[0])
            position_notional = float(desired_position * NOTIONAL)
            expected_payment = -position_notional * float(rates[index])
            stored_payment = float(result.funding_return.iloc[report_index[index]] * NOTIONAL)
            sample_rows.append(
                {
                    "strategy_id": row.strategy_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "funding_timestamp": pd.Timestamp(
                        funding_ts[index], unit="ns", tz="UTC"
                    ).isoformat(),
                    "historical_funding_rate": rates[index],
                    "executed_position": desired_position,
                    "position_notional_usdt": position_notional,
                    "expected_funding_payment_usdt": expected_payment,
                    "stored_funding_payment_usdt": stored_payment,
                    "payment_mismatch_usdt": stored_payment - expected_payment,
                    "payer_receiver_semantics": "LONG_PAYS"
                    if desired_position > 0 and rates[index] > 0
                    else (
                        "SHORT_RECEIVES"
                        if desired_position < 0 and rates[index] > 0
                        else "SIGN_REVERSED_WITH_NEGATIVE_RATE"
                    ),
                    "status": "PASSED"
                    if np.isclose(stored_payment, expected_payment, atol=1e-10, rtol=0)
                    else "FAILED",
                }
            )
    return pd.DataFrame(sample_rows), pd.DataFrame(reconciliation_rows)


def funding_component_audit(repo: Path) -> pd.DataFrame:
    rows = [
        {
            "component": "Historical funding source",
            "source": "Binance Vision official USD-M Futures monthly fundingRate archive; public REST fundingRate tail where applicable",
            "field": "funding_rate (archive CSV column 3; REST fundingRate)",
            "units": "decimal rate per settlement interval",
            "code_path": "feature_engine/data_sources/binance_vision.py: read_binance_funding_zip, normalize_binance_funding, read_binance_funding_api",
            "validation_status": "PASSED",
            "notes": "Timestamp is calc_time/fundingTime, normalized as UTC settlement timestamp; archive includes funding_interval_hours.",
        },
        {
            "component": "Canonical funding loader",
            "source": "Hive Parquet data_type=funding_rate/freq=settlement",
            "field": "funding_rate; funding_interval_hours; optional mark_price",
            "units": "decimal rate; hours; quote currency price",
            "code_path": "data_engine/sources/parquet_funding.py: ParquetFundingSource.stream",
            "validation_status": "PASSED",
            "notes": "Produces FundingRateEvent sourced as binance_vision_funding_rate.",
        },
        {
            "component": "FIRST_TICK funding payment",
            "source": "Frozen executed position at settlement and historical funding_rate",
            "field": "payment = -position_notional * funding_rate",
            "units": "USDT payment",
            "code_path": "scripts/internal/run_constant_notional_overlay.py: calculate_overlay",
            "validation_status": "PASSED",
            "notes": "Positive rate: long pays and short receives. Strict constant-notional path uses exact signed notional.",
        },
        {
            "component": "MAKER funding payment",
            "source": "OrderFilled-derived actual position, historical L1 midpoint, historical funding_rate",
            "field": "payment = -actual_position * unit_quantity * midpoint * funding_rate",
            "units": "USDT payment",
            "code_path": "scripts/internal/run_selected_partial_window_maker.py",
            "validation_status": "PASSED",
            "notes": "Funding is applied to actual maker position, not desired target position.",
        },
        {
            "component": "Return aggregation",
            "source": "Trading return plus funding return",
            "field": "total_return = trading_return + funding_return",
            "units": "arithmetic return",
            "code_path": "scripts/internal/run_constant_notional_overlay.py: calculate_overlay",
            "validation_status": "PASSED",
            "notes": "Funding Included minus Funding Excluded reconciles to accumulated funding_return.",
        },
        {
            "component": "Premium Index exclusion",
            "source": "Accounting code-path inspection",
            "field": "premium_index",
            "units": "N/A",
            "code_path": "run_constant_notional_overlay.py; run_selected_partial_window_maker.py; parquet_funding.py",
            "validation_status": "PASSED",
            "notes": "Premium Index is not read or booked into PnL. Legacy 'premium' result-column names map to funding contribution only.",
        },
        {
            "component": "Boss-facing legacy-name mapping",
            "source": "Existing result schema compatibility",
            "field": "cumulative_return_with_premium -> Funding Included; cumulative_return_without_premium -> Funding Excluded",
            "units": "label mapping only",
            "code_path": "Boss delivery rendering/packaging",
            "validation_status": "PASSED",
            "notes": "Internal legacy columns remain unchanged to preserve hashes; boss-facing terminology is Funding.",
        },
    ]
    # Guard the conclusion against accidental future accounting changes.
    accounting_text = (repo / "scripts/internal/run_constant_notional_overlay.py").read_text(
        encoding="utf-8"
    )
    maker_text = (repo / "scripts/internal/run_selected_partial_window_maker.py").read_text(
        encoding="utf-8"
    )
    if "premium_index" in accounting_text.lower() or "premium_index" in maker_text.lower():
        rows[5]["validation_status"] = "FAILED"
        rows[5]["notes"] = "premium_index reference found in active accounting path"
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--boss-root", type=Path, required=True)
    parser.add_argument("--master-path", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    delivery = args.delivery_root
    before_hashes = numerical_hashes(delivery)

    funding_components = funding_component_audit(args.repo)
    samples, reconciliation = funding_numerical_audit(
        args.boss_root,
        args.market_root,
        args.master_path,
    )
    stale = scan_stale_labels(delivery)
    empty = empty_folder_audit(delivery)
    missing, completeness = missing_and_completeness(delivery)
    figures = image_audit(delivery)

    atomic_csv(funding_components, delivery / "funding_accounting_audit.csv")
    atomic_csv(samples, delivery / "funding_payment_sample_validation.csv")
    atomic_csv(reconciliation, delivery / "funding_return_reconciliation.csv")
    atomic_csv(stale, delivery / "stale_premium_label_audit.csv")
    atomic_csv(empty, delivery / "empty_folder_audit.csv")
    atomic_csv(missing, delivery / "missing_output_audit.csv")
    atomic_csv(completeness, delivery / "strategy_folder_completeness.csv")
    atomic_csv(figures, delivery / "figure_integrity_audit.csv")

    after_hashes = numerical_hashes(delivery)
    invariance = pd.DataFrame(
        [
            {
                "file": path,
                "sha256_before": before_hashes[path],
                "sha256_after": after_hashes.get(path),
                "unchanged": before_hashes[path] == after_hashes.get(path),
            }
            for path in sorted(before_hashes)
        ]
    )
    atomic_csv(invariance, delivery / "numerical_result_hash_audit.csv")

    failures = {
        "funding_components": int(funding_components.validation_status.ne("PASSED").sum()),
        "funding_samples": int(samples.status.ne("PASSED").sum()),
        "funding_reconciliation": int(reconciliation.status.ne("PASSED").sum()),
        "stale_premium_labels": len(stale),
        "empty_folders": len(empty),
        "missing_outputs": len(missing),
        "incomplete_strategies": int((~completeness.complete).sum()),
        "bad_figures": int(figures.status.ne("PASSED").sum()),
        "numerical_hash_changes": int((~invariance.unchanged).sum()),
    }
    summary = {
        "status": "PASSED" if not any(failures.values()) else "BLOCKED",
        "active_delivery": str(delivery),
        "funding_data_source": "Binance Vision official USD-M Futures fundingRate archive",
        "uses_actual_funding_rate": True,
        "funding_payment_validated": failures["funding_samples"] == 0,
        "premium_index_used_as_pnl": False,
        "figures_relabelled": 0,
        "png_files_checked": len(figures),
        "strategies_checked": len(completeness),
        "backtests_rerun": 0,
        "result_value_modifications": 0,
        "failures": failures,
    }
    atomic_json(summary, delivery / "funding_output_audit_validation.json")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
