#!/usr/bin/env python3
"""Extend the canonical original-strategy deliverable with episode diagnostics."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import matplotlib as mpl


mpl.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from results.episode_diagnostics import METRICS
from results.episode_diagnostics import METRIC_SPECS
from results.episode_diagnostics import METRIC_SPEC_BY_ID
from results.episode_diagnostics import EPISODE_SCHEMA_VERSION
from results.episode_diagnostics import SIGNED_METRICS
from results.episode_diagnostics import atomic_parquet
from results.episode_diagnostics import choose_duration_unit
from results.episode_diagnostics import choose_histogram_spec
from results.episode_diagnostics import describe
from results.episode_diagnostics import enrich_episode_frame
from results.episode_diagnostics import histogram_rows
from results.episode_diagnostics import maximum_formula_residual
from results.episode_diagnostics import metric_values
from results.episode_diagnostics import normalize_episode_schema
from results.episode_diagnostics import validate_premium_pair
from results.strategy_evaluation import render_additive_strategy_evaluation


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DELIVERABLE = ROOT / "outputs/deliverables/existing_registered_strategies_current"
SEARCH_ARTIFACTS = (
    ROOT / "outputs/internal_audit/strategy_workbook/phase3a_search_protocol.json",
    ROOT / "outputs/internal_audit/strategy_workbook/phase3a_walk_forward_protocol.json",
    ROOT / "outputs/internal_audit/strategy_workbook/parameter_search_manifest.csv",
)
COLORS = {"included": "#2563eb", "excluded": "#dd7a0c", "execution": "#6a3d9a"}
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_DELIVERABLE)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--strategy", action="append", dest="strategies")
    parser.add_argument("--max-scatter-points", type=int, default=100_000)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--empty-only", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def search_hashes() -> dict[str, str | None]:
    return {str(path.resolve()): sha256(path) for path in SEARCH_ARTIFACTS}


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def discover(root: Path, strategies: set[str] | None) -> list[Path]:
    paths = sorted(root.glob("*/BTCUSDT/*/lag*m/*/per_trade_break_even.csv"))
    paths = [path for path in paths if path.parent.name in {"original", "strict_reverse"}]
    if strategies is not None:
        paths = [path for path in paths if path.relative_to(root).parts[0] in strategies]
    return paths


def is_header_only_csv(path: Path) -> bool:
    with path.open("rb") as stream:
        stream.readline()
        return stream.readline() == b""


def metadata_from_path(source: Path, root: Path) -> dict[str, str]:
    strategy, symbol, timeframe, lag_dir, source_variant, _ = source.relative_to(root).parts
    variant = "normal" if source_variant == "original" else source_variant
    lag_minutes = lag_dir.removeprefix("lag").removesuffix("m")
    return {
        "strategy_id": strategy,
        "direction_mode": variant,
        "variant": variant,
        "source_variant": source_variant,
        "symbol": symbol,
        "timeframe": timeframe,
        "granularity": f"{timeframe} bar",
        "lag": f"{lag_minutes}m physical-time",
        "lag_dir": lag_dir,
    }


def full_metric_arrays(frame: pd.DataFrame, metric: str, divisor: float) -> dict[str, np.ndarray]:
    return {
        mode: metric_values(frame.loc[frame["premium_mode"] == mode], metric, divisor)
        for mode in ("included", "excluded")
    }


def render_diagnostics(  # noqa: C901 - one explicit row per required metric
    *,
    frame: pd.DataFrame,
    bin_frame: pd.DataFrame,
    metadata: dict[str, str],
    destination: Path,
    duration_unit: str,
    duration_divisor: float,
    max_scatter_points: int,
) -> dict[str, int]:
    figure, axes = plt.subplots(4, 2, figsize=(20, 23))
    for row_index in range(1, 4):
        axes[row_index, 0].sharex(axes[0, 0])
    display_counts: dict[str, int] = {}
    modes = ("included", "excluded")
    for row_index, spec in enumerate(METRIC_SPECS):
        metric = spec.metric_id
        time_axis, distribution_axis = axes[row_index]
        divisor = duration_divisor if metric == "holding_duration" else 1.0
        arrays = full_metric_arrays(frame, metric, divisor)
        plot_modes = modes if spec.premium_sensitive else ("included",)
        has_values = any(len(arrays[mode]) for mode in plot_modes)
        for mode in plot_modes:
            selected = frame.loc[frame["premium_mode"] == mode]
            values = arrays[mode]
            stride = max(1, math.ceil(len(selected) / max(1, max_scatter_points)))
            displayed = selected.iloc[::stride]
            displayed_values = values[::stride]
            display_counts[f"{metric}_{mode}"] = len(displayed)
            label = "With premium" if mode == "included" else "Without premium"
            if not spec.premium_sensitive:
                label = "Executed episodes"
            time_axis.scatter(
                displayed["completion_timestamp"],
                displayed_values,
                s=3.0,
                alpha=0.28,
                color=COLORS[mode] if spec.premium_sensitive else COLORS["execution"],
                label=label,
                linewidths=0.0,
                rasterized=True,
            )
        if spec.signed:
            time_axis.axhline(0.0, color="black", linewidth=0.8, alpha=0.7)
        display_unit = duration_unit if metric == "holding_duration" else spec.display_unit
        time_axis.set_ylabel(f"{spec.display_name} ({display_unit})")
        if metric == "episode_turnover":
            time_axis.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=100.0))
        time_axis.grid(alpha=0.2)
        if has_values:
            time_axis.legend(loc="best", fontsize=8)
        else:
            time_axis.text(
                0.5,
                0.5,
                "No completed trading episodes\nCompleted episodes: 0",
                transform=time_axis.transAxes,
                ha="center",
            )

        bar_modes = modes if spec.premium_sensitive else ("included",)
        width_factor = 0.42 if len(bar_modes) == 2 else 0.82
        for mode_index, mode in enumerate(bar_modes):
            if not len(arrays[mode]):
                continue
            rows = bin_frame.loc[
                (bin_frame["metric"] == metric)
                & (bin_frame["premium_mode"] == mode)
                & (bin_frame["bin_kind"] == "central")
            ]
            if rows.empty:
                continue
            centers = rows["bin_center"].to_numpy(dtype="float64")
            counts = rows["count"].to_numpy(dtype="int64")
            bin_width = float(rows["bin_width"].iloc[0])
            offset = (mode_index - (len(bar_modes) - 1) / 2.0) * bin_width * width_factor
            color = COLORS[mode] if spec.premium_sensitive else COLORS["execution"]
            label = "With premium" if mode == "included" else "Without premium"
            if not spec.premium_sensitive:
                label = "Executed episodes"
            distribution_axis.bar(
                centers + offset,
                counts,
                width=bin_width * width_factor,
                alpha=0.28,
                color=color,
                edgecolor=color,
                linewidth=0.35,
                label=f"{label} count",
            )
            distribution_axis.plot(
                centers,
                counts,
                color=color,
                linewidth=1.15,
                marker="o",
                markersize=2.2,
                label=f"{label} frequency polygon",
            )
            overflow_rows = bin_frame.loc[
                (bin_frame["metric"] == metric)
                & (bin_frame["premium_mode"] == mode)
                & (bin_frame["bin_kind"] != "central")
            ]
            annotations = [
                f"{kind}={int(overflow_rows.loc[overflow_rows['bin_kind'] == kind, 'count'].sum())}"
                for kind in ("underflow", "overflow")
            ]
            distribution_axis.text(
                0.99,
                0.98 - mode_index * 0.06,
                f"{label}: " + ", ".join(annotations),
                transform=distribution_axis.transAxes,
                ha="right",
                va="top",
                fontsize=8,
                color=color,
            )
        if spec.signed:
            distribution_axis.axvline(0.0, color="black", linewidth=0.8, alpha=0.7)
        distribution_axis.set_xlabel(f"{spec.display_name} ({display_unit})")
        if metric == "episode_turnover":
            distribution_axis.xaxis.set_major_formatter(mticker.PercentFormatter(xmax=100.0))
        distribution_axis.set_ylabel("Episode Count")
        distribution_axis.grid(axis="y", alpha=0.2)
        if has_values:
            distribution_axis.legend(loc="best", fontsize=7)
        else:
            distribution_axis.text(
                0.5,
                0.5,
                "No completed trading episodes\nCompleted episodes: 0",
                transform=distribution_axis.transAxes,
                ha="center",
            )

    if frame.empty:
        for axis in axes[:, 0]:
            axis.set_xticks([])
    else:
        locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
        for axis in axes[:, 0]:
            axis.xaxis.set_major_locator(locator)
            axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axes[-1, 0].set_xlabel("Episode completion / de-risk time (UTC)")
    figure.suptitle(
        f"{metadata['strategy_id']} / {metadata['direction_mode']} / {metadata['symbol']} / "
        f"{metadata['timeframe']} bar / lag={metadata['lag']} — Per-Episode Diagnostics",
        fontsize=15,
    )
    figure.text(
        0.5,
        0.008,
        "Histogram bars and frequency polygons use the full completed-episode population. "
        "Scatter display may be deterministically thinned; source tables and statistics are never sampled.",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.02, 1, 0.98))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.png")
    try:
        figure.savefig(temporary, dpi=150)
    finally:
        figure.clear()
        plt.close(figure)
        plt.close("all")
    temporary.replace(destination)
    return display_counts


def flatten_summary(
    frame: pd.DataFrame,
    *,
    metadata: dict[str, str],
    premium_mode: str,
    duration_divisor: float,
    duration_unit: str,
    unfinished_count: int,
) -> dict[str, Any]:
    selected = frame.loc[frame["premium_mode"] == premium_mode]
    row: dict[str, Any] = {
        **{
            key: metadata[key]
            for key in ("strategy_id", "direction_mode", "symbol", "timeframe", "lag")
        },
        "premium_mode": premium_mode,
        "completed_episode_count": len(selected),
        "unfinished_episode_count": int(unfinished_count),
        "holding_duration_display_unit": duration_unit,
    }
    for metric in METRICS:
        metric_spec = METRIC_SPEC_BY_ID[metric]
        divisor = duration_divisor if metric == "holding_duration" else 1.0
        values = metric_values(selected, metric, divisor)
        details = describe(values, signed=metric_spec.signed)
        prefix = "episode_turnover_pct" if metric == "episode_turnover" else metric
        for name, value in details.items():
            row[f"{prefix}_{name}"] = value
        if metric == "episode_turnover":
            raw_values = selected["episode_turnover"].to_numpy(dtype="float64")
            for name, value in describe(raw_values).items():
                row[f"episode_turnover_{name}"] = value
            row["episode_turnover_zero_turnover_count"] = int(
                np.sum(np.isclose(raw_values, 0.0))
            )
        if metric == "holding_duration":
            raw_minutes = selected["holding_duration_minutes"].to_numpy(dtype="float64")
            row["holding_duration_median_minutes"] = (
                float(np.median(raw_minutes)) if raw_minutes.size else None
            )
            row["holding_duration_p95_minutes"] = (
                float(np.quantile(raw_minutes, 0.95)) if raw_minutes.size else None
            )
            row["holding_duration_max_minutes"] = (
                float(np.max(raw_minutes)) if raw_minutes.size else None
            )
    return row


def process_one(
    source: Path,
    *,
    source_root: Path,
    output_root: Path,
    max_scatter_points: int,
    overwrite: bool,
) -> dict[str, Any]:
    metadata = metadata_from_path(source, source_root)
    source_case_dir = source.parent
    source_parts = list(source.parent.relative_to(source_root).parts)
    source_parts[-1] = metadata["direction_mode"]
    case_dir = output_root.joinpath(*source_parts)
    destination = (
        case_dir
        / f"{metadata['symbol']}_{metadata['timeframe']}_{metadata['lag_dir']}_{metadata['direction_mode']}_episode_diagnostics.png"
    )
    parquet_path = case_dir / "episode_metrics.parquet"
    bins_path = case_dir / "episode_distribution_bins.csv"
    per_run_summary_path = case_dir / "episode_metric_summary.json"
    if not overwrite and all(
        path.is_file() for path in (destination, parquet_path, bins_path, per_run_summary_path)
    ):
        return {
            "status": "cached",
            "source": str(source),
            "summary_path": str(per_run_summary_path),
            "bins_path": str(bins_path),
        }

    source_hash_before = sha256(source)
    raw = pd.read_csv(source)
    _normalized, schema_metadata = normalize_episode_schema(raw)
    frame = enrich_episode_frame(raw)
    for mode_column in ("variant", "direction_mode"):
        if mode_column in frame.columns:
            frame[mode_column] = metadata["direction_mode"]
    pair = validate_premium_pair(frame)
    formula_residual = maximum_formula_residual(frame)
    duration_unit, duration_divisor = choose_duration_unit(
        frame["holding_duration_seconds"].to_numpy(dtype="float64")
    )
    bin_rows: list[dict[str, Any]] = []
    for metric in METRICS:
        metric_spec = METRIC_SPEC_BY_ID[metric]
        divisor = duration_divisor if metric == "holding_duration" else 1.0
        arrays = full_metric_arrays(frame, metric, divisor)
        combined = np.concatenate([arrays[mode] for mode in ("included", "excluded")])
        spec = choose_histogram_spec(
            combined,
            metric=metric,
            display_unit=duration_unit if metric == "holding_duration" else metric_spec.display_unit,
            signed=metric_spec.signed,
            minimum_width=0.0,
        )
        for mode in ("included", "excluded"):
            for row in histogram_rows(arrays[mode], spec):
                bin_rows.append(
                    {
                        **metadata,
                        "premium_mode": mode,
                        "raw_source_column": metric_spec.source_column,
                        "raw_unit": (
                            "x capital" if metric == "episode_turnover" else spec.display_unit
                        ),
                        "display_transform": (
                            "raw * 100" if metric == "episode_turnover" else "identity"
                        ),
                        **row,
                    }
                )
    bin_frame = pd.DataFrame(bin_rows)
    write_csv_atomic(bin_frame, bins_path)
    atomic_parquet(frame, parquet_path)

    old_summary_path = source_case_dir / "per_trade_break_even_summary.json"
    old_summaries = (
        json.loads(old_summary_path.read_text(encoding="utf-8"))
        if old_summary_path.is_file()
        else {}
    )
    summaries = [
        flatten_summary(
            frame,
            metadata=metadata,
            premium_mode=mode,
            duration_divisor=duration_divisor,
            duration_unit=duration_unit,
            unfinished_count=int(
                old_summaries.get(mode, {}).get("open_unfinished_episode_count", 0)
            ),
        )
        for mode in ("included", "excluded")
    ]
    display_counts = render_diagnostics(
        frame=frame,
        bin_frame=bin_frame,
        metadata=metadata,
        destination=destination,
        duration_unit=duration_unit,
        duration_divisor=duration_divisor,
        max_scatter_points=max_scatter_points,
    )
    validation = {
        "status": "passed",
        "source_episode_count": len(raw),
        "enriched_episode_count": len(frame),
        "completed_episode_count_per_premium": pair["episode_count"],
        "maximum_premium_identity_residual": pair["maximum_identity_residual"],
        "maximum_break_even_residual": formula_residual,
        "old_new_return_residual": float(
            np.max(
                np.abs(raw["delta_gross_return"].to_numpy() - frame["episode_return"].to_numpy()),
                initial=0.0,
            )
        ),
        "old_new_turnover_residual": float(
            np.max(
                np.abs(raw["delta_turnover"].to_numpy() - frame["episode_turnover"].to_numpy()),
                initial=0.0,
            )
        ),
        "old_new_be_residual": float(
            np.max(
                np.abs(raw["break_even_bps"].to_numpy() - frame["break_even_bps"].to_numpy()),
                initial=0.0,
            )
        ),
        "old_new_timestamp_mismatches": int(
            np.sum(
                pd.to_datetime(raw["completion_timestamp"], utc=True).to_numpy()
                != frame["completion_timestamp"].to_numpy()
            )
        ),
        "negative_duration_count": int(np.sum(frame["holding_duration_seconds"] < 0.0)),
        "histogram_row_count": len(bin_frame),
        "histogram_accounting_failures": int(
            sum(
                int(group["count"].sum()) != len(frame.loc[frame["premium_mode"] == premium])
                for (premium, _metric), group in bin_frame.groupby(["premium_mode", "metric"])
            )
        ),
        "display_counts": display_counts,
        "schema": schema_metadata,
        "canonical_source_sha256_before": source_hash_before,
        "canonical_source_sha256_after": sha256(source),
        "canonical_source_hash_unchanged": source_hash_before == sha256(source),
        "render_missing_classification": (
            "TRUE_ZERO_COMPLETED_EPISODES"
            if pair["episode_count"] == 0
            else "OBSERVATIONS_RENDERED"
        ),
    }
    payload = {
        "metadata": metadata,
        "summaries": summaries,
        "validation": validation,
        "source_table": str(source.resolve()),
        "episode_table": str(parquet_path.resolve()),
        "distribution_bins": str(bins_path.resolve()),
        "figure": str(destination.resolve()),
    }
    atomic_json(per_run_summary_path, payload)
    return {
        "status": "generated",
        "source": str(source),
        "summary_path": str(per_run_summary_path),
        "bins_path": str(bins_path),
    }


def load_worker_outputs(
    results: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    bins: list[pd.DataFrame] = []
    validations: list[dict[str, Any]] = []
    for result in results:
        payload = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
        summaries.extend(payload["summaries"])
        bins.append(pd.read_csv(result["bins_path"]))
        validations.append(payload["validation"])
    return pd.DataFrame(summaries), pd.concat(bins, ignore_index=True), validations


def build_plot_validation(
    payloads: list[dict[str, Any]], output_root: Path
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconcile every rendered panel to the canonical completed-episode rows."""
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for payload in payloads:
        metadata = payload["metadata"]
        validation = payload["validation"]
        episode_count = int(validation["completed_episode_count_per_premium"])
        bin_frame = pd.read_csv(payload["distribution_bins"])
        figure_exists = Path(payload["figure"]).is_file()
        for spec in METRIC_SPECS:
            modes = ("included", "excluded") if spec.premium_sensitive else ("included",)
            for mode in modes:
                expected = episode_count
                displayed = int(
                    validation["display_counts"].get(f"{spec.metric_id}_{mode}", 0)
                )
                represented = int(
                    bin_frame.loc[
                        (bin_frame["metric"] == spec.metric_id)
                        & (bin_frame["premium_mode"] == mode),
                        "count",
                    ].sum()
                )
                for panel_type, observed in (
                    ("time_series", displayed),
                    ("distribution", represented),
                ):
                    if expected == 0:
                        status = "VERIFIED_TRUE_ZERO_COMPLETED_EPISODES"
                    elif figure_exists and observed > 0 and (
                        panel_type != "distribution" or observed == expected
                    ):
                        status = "PASSED"
                    else:
                        status = "FAILED_RENDER_MISSING"
                    row = {
                        **{
                            key: metadata[key]
                            for key in (
                                "strategy_id",
                                "direction_mode",
                                "symbol",
                                "timeframe",
                                "lag",
                            )
                        },
                        "metric": spec.metric_id,
                        "premium_mode": mode if spec.premium_sensitive else "shared_execution",
                        "panel_type": panel_type,
                        "raw_source_column": spec.source_column,
                        "display_name": spec.display_name,
                        "display_unit": spec.display_unit,
                        "expected_observation_count": expected,
                        "observed_or_displayed_count": observed,
                        "figure_exists": figure_exists,
                        "status": status,
                    }
                    rows.append(row)
                    if status != "PASSED":
                        missing.append(
                            {
                                **row,
                                "classification": status,
                                "reason": (
                                    "canonical source has zero completed episodes for this direction/lag"
                                    if expected == 0
                                    else "source observations exist but the rendered panel did not reconcile"
                                ),
                            }
                        )
    validation_frame = pd.DataFrame(rows).sort_values(
        ["strategy_id", "timeframe", "lag", "direction_mode", "metric", "panel_type"]
    )
    missing_frame = pd.DataFrame(
        missing,
        columns=[*validation_frame.columns, "classification", "reason"],
    )
    if not missing_frame.empty:
        missing_frame = missing_frame.sort_values(
            ["strategy_id", "timeframe", "lag", "direction_mode", "metric", "panel_type"]
        )
    write_csv_atomic(validation_frame, output_root / "phase_episode_plot_validation.csv")
    write_csv_atomic(validation_frame, output_root / "episode_plot_validation.csv")
    write_csv_atomic(missing_frame, output_root / "episode_render_missing_audit.csv")
    return validation_frame, missing_frame


def build_corrected_canonical_summary(
    source_root: Path, output_root: Path, strategies: set[str] | None = None
) -> pd.DataFrame:
    canonical = pd.read_csv(source_root / "canonical_summary.csv")
    canonical = canonical.loc[canonical["variant"].isin(["original", "strict_reverse"])].copy()
    if strategies is not None:
        canonical = canonical.loc[canonical["strategy"].isin(strategies)].copy()
    canonical["source_variant"] = canonical["variant"]
    canonical["variant"] = canonical["variant"].replace({"original": "normal"})
    canonical["figure_relative"] = canonical.apply(
        lambda row: (
            Path(row["strategy"])
            / row["symbol"]
            / row["timeframe"]
            / f"lag{int(row['lag_minutes'])}m"
            / row["variant"]
            / f"{row['symbol']}_{row['timeframe']}_lag{int(row['lag_minutes'])}m_"
            f"{row['variant']}_performance.png"
        ).as_posix(),
        axis=1,
    )
    canonical["figure"] = canonical["figure_relative"].map(
        lambda relative: str((output_root / relative).resolve())
    )
    if set(canonical["variant"].unique()) != {"normal", "strict_reverse"}:
        raise AssertionError("corrected boss summary contains an invalid direction universe")
    write_csv_atomic(canonical, output_root / "canonical_summary.csv")
    return canonical


def build_html(output_root: Path, canonical: pd.DataFrame, summary: pd.DataFrame) -> Path:
    run_rows = summary.drop_duplicates(["strategy_id", "direction_mode", "timeframe", "lag"])
    links: list[dict[str, str]] = []
    for row in run_rows.to_dict("records"):
        lag_minutes = str(row["lag"]).split("m", 1)[0]
        relative_dir = (
            Path(row["strategy_id"])
            / row["symbol"]
            / row["timeframe"]
            / f"lag{lag_minutes}m"
            / row["direction_mode"]
        )
        links.append(
            {
                "strategy": row["strategy_id"],
                "variant": row["direction_mode"],
                "timeframe": row["timeframe"],
                "lag_minutes": int(lag_minutes),
                "episode_diagnostics": (
                    relative_dir
                    / f"{row['symbol']}_{row['timeframe']}_lag{lag_minutes}m_{row['direction_mode']}_episode_diagnostics.png"
                ).as_posix(),
                "episode_bins": (relative_dir / "episode_distribution_bins.csv").as_posix(),
            }
        )
    link_frame = pd.DataFrame(links)
    canonical["lag_minutes"] = canonical["lag"].str.extract(r"^(\d+)m").astype(int)
    merged = canonical.merge(
        link_frame, on=["strategy", "variant", "timeframe", "lag_minutes"], how="left"
    )
    episode_columns = [
        "strategy_id",
        "direction_mode",
        "timeframe",
        "lag",
        "premium_mode",
        "completed_episode_count",
        "unfinished_episode_count",
        "break_even_bps_median",
        "break_even_bps_positive_fraction",
        "episode_return_bps_median",
        "episode_turnover_median",
        "episode_turnover_pct_median",
        "holding_duration_median_minutes",
    ]
    episode_summary = summary[episode_columns].rename(
        columns={
            "strategy_id": "strategy",
            "direction_mode": "variant",
            "premium_mode": "premium",
        }
    )
    episode_summary["lag_minutes"] = (
        episode_summary.pop("lag").str.extract(r"^(\d+)m").astype(int)
    )
    merged = merged.merge(
        episode_summary,
        on=["strategy", "variant", "timeframe", "lag_minutes", "premium"],
        how="left",
    )
    for column, label in (
        ("figure_relative", "performance"),
        ("episode_diagnostics", "episode diagnostics"),
        ("episode_bins", "distribution bins"),
    ):
        if column in merged:
            merged[column] = merged[column].map(
                lambda value, link_label=label: f'<a href="{value}">{link_label}</a>'
            )
    destination = output_root / "episode_diagnostics_summary.html"
    destination.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Episode diagnostics</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;font-size:12px}"
        "th,td{border:1px solid #ddd;padding:5px}th{position:sticky;top:0;background:#f4f4f4}</style></head><body>"
        "<h1>Original Strategy Per-Episode Diagnostics</h1>"
        "<p>Chronological scatter + count histogram + frequency polygon. All statistics use full completed episodes.</p>"
        + merged.to_html(index=False, escape=False, float_format=lambda value: f"{value:.8g}")
        + "</body></html>",
        encoding="utf-8",
    )
    shutil.copy2(destination, output_root / "canonical_summary.html")
    return destination


def materialize_performance_figures(
    source_root: Path, output_root: Path, sources: list[Path]
) -> int:
    """Make the revised HTML self-contained without duplicating data when hardlinks work."""
    count = 0
    for episode_source in sources:
        metadata = metadata_from_path(episode_source, source_root)
        source_case = episode_source.parent
        source_evaluation = source_case / "strategy_evaluation.parquet"
        source_metrics = source_case / "metrics.json"
        if not source_evaluation.is_file() or not source_metrics.is_file():
            raise RuntimeError(f"missing trusted performance source beside {episode_source}")
        relative_parts = list(source_case.relative_to(source_root).parts)
        relative_parts[-1] = metadata["direction_mode"]
        destination_dir = output_root.joinpath(*relative_parts)
        destination = destination_dir / (
            f"{metadata['symbol']}_{metadata['timeframe']}_{metadata['lag_dir']}_"
            f"{metadata['direction_mode']}_performance.png"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        series = pd.read_parquet(source_evaluation).to_dict("records")
        metrics_payload = json.loads(source_metrics.read_text(encoding="utf-8"))
        render_additive_strategy_evaluation(
            series,
            metrics_payload["cases"],
            destination=destination,
            run_name=(
                f"{metadata['strategy_id']} / {metadata['direction_mode'].upper()} / "
                f"{metadata['symbol']} / {metadata['timeframe']}"
            ),
            lag_label=metadata["lag"],
        )
        count += 1
    return count


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_root = (
        args.output_root.resolve()
        if args.output_root
        else source_root.with_name(source_root.name + "_episode_diagnostics_v2")
    )
    output_root.mkdir(parents=True, exist_ok=True)
    before = search_hashes()
    strategy_filter = set(args.strategies) if args.strategies else None
    sources = discover(source_root, strategy_filter)
    if args.empty_only:
        sources = [source for source in sources if is_header_only_csv(source)]
    if not sources:
        raise ValueError("no canonical per-trade episode tables discovered")
    results: list[dict[str, Any]] = []
    workers = max(1, min(int(args.workers), len(sources)))
    kwargs = {
        "source_root": source_root,
        "output_root": output_root,
        "max_scatter_points": int(args.max_scatter_points),
        "overwrite": bool(args.overwrite),
    }
    if workers == 1:
        for index, source in enumerate(sources, start=1):
            results.append(process_one(source, **kwargs))
            print(f"EPISODE_DIAGNOSTIC {index}/{len(sources)} {source.parent}", flush=True)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_one, source, **kwargs): source for source in sources}
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                result = future.result()
                results.append(result)
                print(f"EPISODE_DIAGNOSTIC {index}/{len(sources)} {result['source']}", flush=True)

    summary, bins, validations = load_worker_outputs(results)
    summary = summary.sort_values(
        ["strategy_id", "timeframe", "lag", "direction_mode", "premium_mode"]
    )
    bins = bins.sort_values(
        ["strategy_id", "timeframe", "lag", "direction_mode", "premium_mode", "metric", "bin_left"]
    )
    write_csv_atomic(summary, output_root / "episode_metric_summary.csv")
    write_csv_atomic(bins, output_root / "episode_distribution_bins.csv")
    performance_figure_count = materialize_performance_figures(
        source_root, output_root, sources
    )
    corrected_canonical = build_corrected_canonical_summary(
        source_root, output_root, strategy_filter
    )
    html = build_html(output_root, corrected_canonical, summary)
    after = search_hashes()
    search_integrity = before == after
    payloads = [
        json.loads(Path(result["summary_path"]).read_text(encoding="utf-8")) for result in results
    ]
    plot_validation, missing_audit = build_plot_validation(payloads, output_root)
    figure_count = sum(Path(payload["figure"]).is_file() for payload in payloads)
    episode_table_count = sum(Path(payload["episode_table"]).is_file() for payload in payloads)
    maximums = {
        key: max((float(item[key]) for item in validations), default=0.0)
        for key in (
            "maximum_break_even_residual",
            "maximum_premium_identity_residual",
            "old_new_return_residual",
            "old_new_turnover_residual",
            "old_new_be_residual",
        )
    }
    validation = {
        "status": "passed",
        "original_strategy_count": int(summary["strategy_id"].nunique()),
        "result_unit_count": len(sources),
        "premium_summary_rows": len(summary),
        "episode_diagnostic_figure_count": figure_count,
        "missing_diagnostic_figure_count": len(sources) - figure_count,
        "episode_table_count": episode_table_count,
        "performance_figure_count": performance_figure_count,
        "plot_validation_row_count": len(plot_validation),
        "plot_validation_failure_count": int(
            np.sum(plot_validation["status"] == "FAILED_RENDER_MISSING")
        ),
        "canonical_source_hash_change_count": int(
            sum(not item["canonical_source_hash_unchanged"] for item in validations)
        ),
        "verified_true_zero_panel_count": int(
            np.sum(plot_validation["status"] == "VERIFIED_TRUE_ZERO_COMPLETED_EPISODES")
        ),
        "render_missing_audit_row_count": len(missing_audit),
        "source_episode_rows_including_premium_pairs": int(
            sum(item["source_episode_count"] for item in validations)
        ),
        "completed_episode_rows_per_premium": int(
            sum(item["completed_episode_count_per_premium"] for item in validations)
        ),
        "distribution_bin_rows": len(bins),
        "be_time_series_panels": figure_count,
        "be_distribution_panels": figure_count,
        "return_time_series_panels": figure_count,
        "return_distribution_panels": figure_count,
        "turnover_time_series_panels": figure_count,
        "turnover_distribution_panels": figure_count,
        "duration_time_series_panels": figure_count,
        "duration_distribution_panels": figure_count,
        "histogram_accounting_failures": int(
            sum(item["histogram_accounting_failures"] for item in validations)
        ),
        "negative_duration_count": int(
            sum(item["negative_duration_count"] for item in validations)
        ),
        "old_new_timestamp_mismatches": int(
            sum(item["old_new_timestamp_mismatches"] for item in validations)
        ),
        **maximums,
        "search_artifact_hashes_before": before,
        "search_artifact_hashes_after": after,
        "phase3_search_integrity": search_integrity,
        "parameter_optimization_executed": False,
        "html_index": str(html),
    }
    if not (
        validation["original_strategy_count"]
        == int(corrected_canonical["strategy"].nunique())
        and validation["result_unit_count"] == len(sources)
        and validation["premium_summary_rows"] == 2 * len(sources)
        and validation["episode_diagnostic_figure_count"] == len(sources)
        and validation["episode_table_count"] == len(sources)
        and validation["performance_figure_count"] == len(sources)
        and set(summary["direction_mode"].unique()) == {"normal", "strict_reverse"}
        and set(corrected_canonical["variant"].unique()) == {"normal", "strict_reverse"}
        and validation["plot_validation_failure_count"] == 0
        and validation["canonical_source_hash_change_count"] == 0
        and validation["histogram_accounting_failures"] == 0
        and validation["negative_duration_count"] == 0
        and validation["old_new_timestamp_mismatches"] == 0
        and search_integrity
    ):
        validation["status"] = "failed"
    atomic_json(output_root / "episode_diagnostics_validation.json", validation)
    atomic_json(output_root / "validation_summary.json", validation)
    atomic_json(
        output_root / "episode_diagnostics_artifact_manifest.json",
        {
            "source_result_root": str(source_root),
            "revised_result_root": str(output_root),
            "episode_schema_version": EPISODE_SCHEMA_VERSION,
            "result_units": len(sources),
            "direction_modes": sorted(summary["direction_mode"].unique()),
            "timeframes": sorted(summary["timeframe"].unique()),
            "lags": sorted(summary["lag"].unique()),
            "premium_modes": sorted(summary["premium_mode"].unique()),
            "metrics": list(METRICS),
            "validation": validation,
        },
    )
    print(json.dumps(validation, indent=2))
    return 0 if validation["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
