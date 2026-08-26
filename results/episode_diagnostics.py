"""
Auditable per-episode metrics, distributions, and boss-facing diagnostics.

This module only enriches rows produced by :mod:`results.trade_episode`.  It
never segments positions or replays strategy signals, so all four diagnostics
share the canonical completed-episode identity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


EPISODE_SCHEMA_VERSION = "episode_diagnostics.v2"


@dataclass(frozen=True)
class MetricSpec:
    """One source of truth for episode metric extraction and presentation."""

    metric_id: str
    source_column: str
    display_name: str
    display_unit: str
    display_scale: float
    signed: bool
    premium_sensitive: bool


METRIC_SPECS = (
    MetricSpec(
        "break_even_bps",
        "break_even_bps",
        "Signed Break-even Cost",
        "bps",
        1.0,
        True,
        True,
    ),
    MetricSpec(
        "episode_return_bps",
        "episode_return_bps",
        "Episode Gross Return (1x)",
        "bps",
        1.0,
        True,
        True,
    ),
    MetricSpec(
        "episode_turnover",
        "episode_turnover",
        "Episode Turnover",
        "% of capital",
        100.0,
        False,
        False,
    ),
    MetricSpec(
        "holding_duration",
        "holding_duration_seconds",
        "Holding Duration",
        "dynamic",
        1.0,
        False,
        False,
    ),
)
METRIC_SPEC_BY_ID = {spec.metric_id: spec for spec in METRIC_SPECS}
METRICS = tuple(spec.metric_id for spec in METRIC_SPECS)
SIGNED_METRICS = frozenset(spec.metric_id for spec in METRIC_SPECS if spec.signed)
PAIR_IDENTITY_COLUMNS = (
    "episode_id",
    "side",
    "start_timestamp",
    "completion_timestamp",
    "start_position",
    "maximum_abs_position",
    "end_position",
    "completion_reason",
    "delta_turnover",
)


@dataclass(frozen=True)
class HistogramSpec:
    metric: str
    display_unit: str
    bin_width: float
    display_min: float
    display_max: float
    edges: tuple[float, ...]
    display_range_rule: str


SCHEMA_ALIASES = {
    "strategy_id": ("strategy",),
    "variant": ("direction_mode",),
    "start_timestamp": ("start_time", "entry_timestamp"),
    "completion_timestamp": ("completion_time", "exit_timestamp"),
    "maximum_abs_position": ("max_abs_position",),
    "delta_turnover": ("episode_turnover", "turnover_delta"),
    "delta_gross_return": ("episode_return", "gross_return_delta"),
}
REQUIRED_SOURCE_COLUMNS = frozenset(
    {
        "start_timestamp",
        "completion_timestamp",
        "maximum_abs_position",
        "delta_turnover",
        "delta_gross_return",
        "break_even_bps",
        "premium_mode",
    }
)


def normalize_episode_schema(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Normalize explicitly supported historical aliases; reject unknown schemas."""
    result = frame.copy()
    aliases_used: dict[str, str] = {}
    for canonical, aliases in SCHEMA_ALIASES.items():
        if canonical in result.columns:
            continue
        matches = [alias for alias in aliases if alias in result.columns]
        if len(matches) > 1:
            raise ValueError(f"ambiguous aliases for {canonical}: {matches}")
        if matches:
            result = result.rename(columns={matches[0]: canonical})
            aliases_used[matches[0]] = canonical
    missing = sorted(REQUIRED_SOURCE_COLUMNS - set(result.columns))
    if missing:
        raise ValueError(
            f"unsupported episode schema; missing required columns: {', '.join(missing)}"
        )
    return result, {
        "schema_version": EPISODE_SCHEMA_VERSION,
        "aliases_used": aliases_used,
        "source_columns": sorted(frame.columns),
    }


def enrich_episode_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the requested four-metric fields without changing canonical values."""
    result, _schema = normalize_episode_schema(frame)
    for column in ("start_timestamp", "completion_timestamp"):
        result[column] = pd.to_datetime(result[column], utc=True)
    duration = (result["completion_timestamp"] - result["start_timestamp"]).dt.total_seconds()
    if (duration < 0.0).any():
        raise ValueError("episode completion precedes episode start")
    result["holding_duration_seconds"] = duration.astype("float64")
    result["holding_duration_minutes"] = duration / 60.0
    result["holding_duration_hours"] = duration / 3600.0
    result["episode_return"] = result["delta_gross_return"].astype("float64")
    result["episode_return_bps"] = result["episode_return"] * 10_000.0
    result["episode_turnover"] = result["delta_turnover"].astype("float64")
    # Display-only representation. Accounting and BE continue to use the raw
    # x-capital value in ``episode_turnover`` / ``delta_turnover``.
    result["episode_turnover_pct"] = result["episode_turnover"] * 100.0
    result["max_abs_position"] = result["maximum_abs_position"].astype("float64")
    return result


def maximum_formula_residual(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    residual = (
        frame["episode_return"].to_numpy(dtype="float64")
        - frame["episode_turnover"].to_numpy(dtype="float64")
        * frame["break_even_bps"].to_numpy(dtype="float64")
        / 10_000.0
    )
    return float(np.max(np.abs(residual), initial=0.0))


def validate_premium_pair(frame: pd.DataFrame, tolerance: float = 1e-12) -> dict[str, Any]:
    """Verify premium modes share one executed episode structure."""
    included = frame.loc[frame["premium_mode"] == "included"].sort_values("episode_id")
    excluded = frame.loc[frame["premium_mode"] == "excluded"].sort_values("episode_id")
    if len(included) != len(excluded):
        raise ValueError("premium modes have different completed episode counts")
    if included.empty:
        return {"episode_count": 0, "maximum_identity_residual": 0.0}
    if included["episode_id"].tolist() != excluded["episode_id"].tolist():
        raise ValueError("premium modes have different episode IDs")
    maximum_residual = 0.0
    for column in PAIR_IDENTITY_COLUMNS:
        left = included[column].reset_index(drop=True)
        right = excluded[column].reset_index(drop=True)
        if pd.api.types.is_numeric_dtype(left):
            residual = float(
                np.max(
                    np.abs(left.to_numpy(dtype="float64") - right.to_numpy(dtype="float64")),
                    initial=0.0,
                )
            )
            maximum_residual = max(maximum_residual, residual)
            if residual > tolerance:
                raise ValueError(f"premium pair mismatch in {column}: {residual}")
        elif not left.equals(right):
            raise ValueError(f"premium pair mismatch in {column}")
    return {
        "episode_count": len(included),
        "maximum_identity_residual": maximum_residual,
    }


def nice_step(span: float, *, target_bins: int = 60, minimum: float = 0.0) -> float:
    """Return a deterministic 1/2/5 x 10^k interval width."""
    if not math.isfinite(span) or span <= 0.0:
        return max(1.0, minimum)
    raw = span / max(1, target_bins)
    exponent = math.floor(math.log10(raw))
    scale = 10.0**exponent
    fraction = raw / scale
    multiplier = (
        1.0 if fraction <= 1.0 else 2.0 if fraction <= 2.0 else 5.0 if fraction <= 5.0 else 10.0
    )
    return max(multiplier * scale, minimum)


def choose_duration_unit(seconds: np.ndarray) -> tuple[str, float]:
    finite = seconds[np.isfinite(seconds)]
    p95 = float(np.quantile(finite, 0.95)) if finite.size else 0.0
    if p95 < 3.0 * 3600.0:
        return "minutes", 60.0
    if p95 < 3.0 * 86400.0:
        return "hours", 3600.0
    return "days", 86400.0


def choose_histogram_spec(
    values: np.ndarray,
    *,
    metric: str,
    display_unit: str,
    signed: bool,
    minimum_width: float = 0.0,
    target_bins: int = 60,
) -> HistogramSpec:
    """Choose robust, recorded display bins while retaining overflow counts."""
    finite = np.asarray(values, dtype="float64")
    finite = finite[np.isfinite(finite)]
    rule = "central display uses p0.5-p99.5; all values retained in under/overflow bins"
    if finite.size == 0:
        low, high, width = (
            (-1.0, 1.0, max(1.0, minimum_width)) if signed else (0.0, 1.0, max(1.0, minimum_width))
        )
    elif signed:
        lower, upper = np.quantile(finite, [0.005, 0.995])
        extent = max(abs(float(lower)), abs(float(upper)), minimum_width or 1e-12)
        width = nice_step(2.0 * extent, target_bins=target_bins, minimum=minimum_width)
        extent = max(width, math.ceil(extent / width) * width)
        low, high = -extent, extent
    else:
        if float(np.min(finite)) < -1e-12:
            raise ValueError(f"non-negative metric {metric} contains negative values")
        upper = max(float(np.quantile(finite, 0.995)), minimum_width or 1e-12)
        width = nice_step(upper, target_bins=target_bins, minimum=minimum_width)
        low, high = 0.0, max(width, math.ceil(upper / width) * width)
    edges = np.arange(low, high + width * 0.5, width, dtype="float64")
    if edges.size < 2:
        edges = np.array([low, low + width], dtype="float64")
    if signed and not np.any(np.isclose(edges, 0.0, atol=width * 1e-10)):
        raise AssertionError("signed histogram is not zero anchored")
    return HistogramSpec(
        metric=metric,
        display_unit=display_unit,
        bin_width=float(width),
        display_min=float(edges[0]),
        display_max=float(edges[-1]),
        edges=tuple(float(value) for value in edges),
        display_range_rule=rule,
    )


def histogram_rows(values: np.ndarray, spec: HistogramSpec) -> list[dict[str, Any]]:
    """Return exact central and overflow counts for one metric distribution."""
    finite = np.asarray(values, dtype="float64")
    finite = finite[np.isfinite(finite)]
    edges = np.asarray(spec.edges, dtype="float64")
    underflow = int(np.sum(finite < edges[0]))
    overflow = int(np.sum(finite > edges[-1]))
    central = finite[(finite >= edges[0]) & (finite <= edges[-1])]
    counts, _ = np.histogram(central, bins=edges)
    raw: list[tuple[float, float, float, int, str]] = []
    if underflow:
        raw.append((-math.inf, edges[0], edges[0], underflow, "underflow"))
    raw.extend(
        (left, right, (left + right) / 2.0, int(count), "central")
        for left, right, count in zip(edges[:-1], edges[1:], counts, strict=True)
    )
    if overflow:
        raw.append((edges[-1], math.inf, edges[-1], overflow, "overflow"))
    total = int(finite.size)
    cumulative = 0
    rows: list[dict[str, Any]] = []
    for left, right, center, count, kind in raw:
        cumulative += count
        rows.append(
            {
                "metric": spec.metric,
                "display_unit": spec.display_unit,
                "bin_width": spec.bin_width,
                "bin_left": left,
                "bin_right": right,
                "bin_center": center,
                "count": count,
                "fraction": count / total if total else 0.0,
                "cumulative_fraction": cumulative / total if total else 0.0,
                "bin_kind": kind,
                "display_range_rule": spec.display_range_rule,
            }
        )
    if sum(row["count"] for row in rows) != total:
        raise AssertionError("histogram rows do not reconcile to episode count")
    return rows


def describe(values: np.ndarray, *, signed: bool = False) -> dict[str, Any]:
    finite = np.asarray(values, dtype="float64")
    finite = finite[np.isfinite(finite)]
    result: dict[str, Any] = {"count": int(finite.size)}
    names = ("min", "p01", "p05", "p25", "median", "p75", "p95", "p99", "max")
    if finite.size:
        quantiles = np.quantile(finite, [0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0])
        result.update(dict(zip(names, (float(value) for value in quantiles), strict=True)))
        result["mean"] = float(np.mean(finite))
        result["std"] = float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0
    else:
        result.update(dict.fromkeys(names))
        result.update({"mean": None, "std": None})
    if signed:
        positive = int(np.sum(finite > 0.0))
        negative = int(np.sum(finite < 0.0))
        zero = int(finite.size - positive - negative)
        result.update(
            {
                "positive_count": positive,
                "zero_count": zero,
                "negative_count": negative,
                "positive_fraction": positive / finite.size if finite.size else 0.0,
                "negative_fraction": negative / finite.size if finite.size else 0.0,
            }
        )
    return result


def metric_values(frame: pd.DataFrame, metric: str, duration_divisor: float = 1.0) -> np.ndarray:
    spec = METRIC_SPEC_BY_ID[metric]
    values = frame[spec.source_column].to_numpy(dtype="float64") * spec.display_scale
    return values / duration_divisor if metric == "holding_duration" else values


def atomic_parquet(frame: pd.DataFrame, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(path)
    return path
