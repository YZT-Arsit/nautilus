"""
FeatureDataset — load historical feature data for model training.

This module provides the read interface between the Feature Data Layer
(OfflineFeatureStore) and a training pipeline.  No model is trained here.

Point-in-time correctness
    By default, ``load_feature_dataset`` excludes warmup rows (is_warmup=True).
    This prevents look-ahead bias: features computed during the warmup phase
    use data that would not be available at prediction time in production.

Join modes
    concat (default)
        Vertically stack all rows from all requested feature sets.
        Backward-compatible with the original single-feature-set API.
        Useful for feature sets that share the same schema or when the caller
        handles merging externally.

    exact
        Inner join on ``join_keys`` (default: [instrument_id, ts_event]).
        Feature columns are prefixed with ``{feature_set_id}__`` to avoid
        conflicts.  Only rows present in ALL feature sets are kept.
        Use when all feature sets are computed at the same bar timestamps.

    asof
        Time-ordered left join: for each row in the primary (first)
        feature set, find the most-recent row in every secondary feature set
        whose ts_event ≤ primary ts_event (per instrument).
        Implemented via pandas.merge_asof; direction="backward".
        Ensures point-in-time correctness — never uses future features.

Column naming (exact / asof modes)
    Required metadata columns (ts_event, instrument_id, is_warmup) are kept
    once without prefix.  All other columns are prefixed with the feature_set_id
    they originate from, e.g. ``vwm_features_v1__momentum``.

select_columns
    • None            — all columns
    • list[str]       — same filter applied to every feature set (original names)
    • dict[str, list] — per-feature-set column lists keyed by feature_set_id

label_path / label_join_mode
    label_path    — path to a Parquet file with label rows
    label_join_mode — "asof" joins labels to the result DataFrame by ts_event
                      per instrument; None = no label join (interface reserved)

Future integration
    Once a model registry exists, FeatureDatasetSpec will be extended with a
    label_spec field describing how to join feature rows with labels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_ext.features.feature_store import OfflineFeatureStore

_REQUIRED_COLS = {"ts_event", "instrument_id", "feature_set_id", "feature_version", "is_warmup"}
_JOIN_KEEP_COLS = {"ts_event", "instrument_id", "is_warmup"}  # columns kept once in joined output


@dataclass
class FeatureDatasetSpec:
    """Specification for loading a training / evaluation feature dataset.

    Parameters
    ----------
    feature_store_path : str | Path
        Root path of the OfflineFeatureStore.
    feature_set_ids : list[str]
        One or more feature set identifiers.
    instruments : list[str] | None
        If None, returns all instruments.
    start : int | None
        Millisecond POSIX timestamp lower bound (inclusive).
    end : int | None
        Millisecond POSIX timestamp upper bound (inclusive).
    include_warmup : bool
        If True, include warmup rows.  Default False preserves point-in-time
        correctness.
    select_columns : None | list[str] | dict[str, list[str]]
        Column filter.  None = all columns.  list[str] = same filter for every
        feature set.  dict[str, list[str]] = per-feature-set filter (keyed by
        feature_set_id; feature sets not in the dict get all columns).
        Required metadata columns are always kept.
    join_mode : str
        "concat" (default) | "exact" | "asof".  See module docstring.
    join_keys : list[str]
        Columns used as join keys for exact mode.  Default ["instrument_id", "ts_event"].
    column_prefix : bool
        If True (default), prefix feature columns with ``{feature_set_id}__`` in
        exact/asof output to avoid name conflicts across feature sets.
    label_path : str | Path | None
        Optional path to a Parquet label file.
    label_join_mode : str | None
        "asof" or None.  If "asof", labels are left-joined to the result by
        ts_event per instrument after the feature join.
    """

    feature_store_path: str | Path
    feature_set_ids: list[str]
    instruments: list[str] | None = None
    start: int | None = None
    end: int | None = None
    include_warmup: bool = False
    select_columns: list[str] | dict[str, list[str]] | None = None
    join_mode: str = "concat"
    join_keys: list[str] = field(default_factory=lambda: ["instrument_id", "ts_event"])
    column_prefix: bool = True
    label_path: str | Path | None = None
    label_join_mode: str | None = None


@dataclass
class FeatureDatasetResult:
    """Result of :func:`load_feature_dataset_with_metadata`.

    Attributes
    ----------
    data : pd.DataFrame
        The assembled feature DataFrame.
    used_feature_sets : list[str]
        Feature set IDs that contributed rows.
    row_count : int
        Number of rows in ``data``.
    columns : list[str]
        Column names of ``data``.
    start : int | None
        Minimum ts_event in ``data``, or None if empty.
    end : int | None
        Maximum ts_event in ``data``, or None if empty.
    """

    data: pd.DataFrame
    used_feature_sets: list[str]
    row_count: int
    columns: list[str]
    start: int | None
    end: int | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _col_filter_for(
    feature_set_id: str,
    select_columns: list[str] | dict[str, list[str]] | None,
) -> list[str] | None:
    """Resolve the column filter for one feature set."""
    if select_columns is None:
        return None
    if isinstance(select_columns, list):
        return select_columns
    # dict: keyed by feature_set_id
    return select_columns.get(feature_set_id)  # None means "all" for this set


def _load_one(
    store: OfflineFeatureStore,
    feature_set_id: str,
    instruments: list[str] | None,
    start: int | None,
    end: int | None,
    include_warmup: bool,
    columns: list[str] | None,
) -> pd.DataFrame:
    """Load one feature set from the store; returns empty DataFrame on miss."""
    parts: list[pd.DataFrame] = []
    if instruments:
        for iid in instruments:
            df = store.query(
                instrument_id=iid,
                feature_set_id=feature_set_id,
                start=start,
                end=end,
                include_warmup=include_warmup,
                columns=columns,
            )
            if not df.empty:
                parts.append(df)
    else:
        df = store.query(
            feature_set_id=feature_set_id,
            start=start,
            end=end,
            include_warmup=include_warmup,
            columns=columns,
        )
        if not df.empty:
            parts.append(df)

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _prefix_feature_cols(df: pd.DataFrame, feature_set_id: str) -> pd.DataFrame:
    """Rename non-metadata columns with feature_set_id prefix."""
    rename = {
        c: f"{feature_set_id}__{c}"
        for c in df.columns
        if c not in _JOIN_KEEP_COLS
    }
    return df.rename(columns=rename)


def _exact_join(
    frames: dict[str, pd.DataFrame],
    join_keys: list[str],
    column_prefix: bool,
) -> pd.DataFrame:
    """Inner join on join_keys across all feature sets."""
    feature_set_ids = list(frames.keys())
    primary_fsid = feature_set_ids[0]

    # Prepare primary: keep join_keys + feature cols
    left = frames[primary_fsid].copy()
    if column_prefix:
        left = _prefix_feature_cols(left, primary_fsid)

    for fsid in feature_set_ids[1:]:
        right = frames[fsid].copy()
        if column_prefix:
            right = _prefix_feature_cols(right, fsid)
        # Drop metadata columns that are already in left (avoid _x/_y suffixes)
        right_drop = [c for c in right.columns if c in left.columns and c not in join_keys]
        right = right.drop(columns=right_drop, errors="ignore")
        left = left.merge(right, on=join_keys, how="inner")

    return left.sort_values("ts_event").reset_index(drop=True)


def _asof_join(
    frames: dict[str, pd.DataFrame],
    column_prefix: bool,
) -> pd.DataFrame:
    """Left asof join: for each primary row find the latest secondary row by ts_event.

    Processes each instrument independently to avoid cross-instrument contamination.
    direction="backward" guarantees no future-data leakage (point-in-time safe).
    """
    feature_set_ids = list(frames.keys())
    primary_fsid = feature_set_ids[0]

    # Collect all instruments across all frames
    all_instruments: set[str] = set()
    for df in frames.values():
        if "instrument_id" in df.columns:
            all_instruments.update(df["instrument_id"].unique())

    instrument_parts: list[pd.DataFrame] = []
    for iid in sorted(all_instruments):
        left = frames[primary_fsid]
        left = left[left["instrument_id"] == iid].sort_values("ts_event").copy()
        if left.empty:
            continue
        if column_prefix:
            left = _prefix_feature_cols(left, primary_fsid)

        for fsid in feature_set_ids[1:]:
            right = frames[fsid]
            right = right[right["instrument_id"] == iid].sort_values("ts_event").copy()
            if right.empty:
                continue
            if column_prefix:
                right = _prefix_feature_cols(right, fsid)
            # Drop columns already in left except ts_event
            right_drop = [
                c for c in right.columns
                if c in left.columns and c != "ts_event"
            ]
            right = right.drop(columns=right_drop, errors="ignore")
            left = pd.merge_asof(
                left, right,
                on="ts_event",
                direction="backward",  # never use future features
            )

        instrument_parts.append(left)

    if not instrument_parts:
        return pd.DataFrame()
    return pd.concat(instrument_parts, ignore_index=True).sort_values("ts_event").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_feature_dataset(spec: FeatureDatasetSpec) -> pd.DataFrame:
    """Load feature data from OfflineFeatureStore as a training DataFrame.

    Behaviour depends on ``spec.join_mode``:

    - ``"concat"`` — vertically stack all feature sets (backward-compatible).
    - ``"exact"``  — inner join on join_keys; columns prefixed per feature set.
    - ``"asof"``   — time-ordered left asof join per instrument; point-in-time safe.

    Examples
    --------
    >>> # Single feature set (backward-compatible)
    >>> spec = FeatureDatasetSpec(
    ...     feature_store_path="outputs/features",
    ...     feature_set_ids=["vwm_features_v1"],
    ...     instruments=["BTCUSDT-PERP.BINANCE"],
    ...     start=1_700_000_000_000,
    ...     end=1_701_000_000_000,
    ... )
    >>> df = load_feature_dataset(spec)

    >>> # Multi feature set, exact join
    >>> spec = FeatureDatasetSpec(
    ...     feature_store_path="outputs/features",
    ...     feature_set_ids=["vwm_features_v1", "vol_features_v1"],
    ...     join_mode="exact",
    ... )
    >>> df = load_feature_dataset(spec)
    >>> # Columns: ts_event, instrument_id, vwm_features_v1__momentum, vol_features_v1__iv, ...
    """
    store = OfflineFeatureStore(spec.feature_store_path)
    join_mode = spec.join_mode

    if join_mode == "concat":
        # --- backward-compatible vertical stack --------------------------
        parts: list[pd.DataFrame] = []
        for fsid in spec.feature_set_ids:
            cols = _col_filter_for(fsid, spec.select_columns)
            df = _load_one(store, fsid, spec.instruments, spec.start, spec.end,
                           spec.include_warmup, cols)
            if not df.empty:
                parts.append(df)

        if not parts:
            return pd.DataFrame()

        result = pd.concat(parts, ignore_index=True).sort_values("ts_event").reset_index(drop=True)

    elif join_mode in ("exact", "asof"):
        # --- load each feature set separately ----------------------------
        frames: dict[str, pd.DataFrame] = {}
        for fsid in spec.feature_set_ids:
            cols = _col_filter_for(fsid, spec.select_columns)
            df = _load_one(store, fsid, spec.instruments, spec.start, spec.end,
                           spec.include_warmup, cols)
            if not df.empty:
                frames[fsid] = df

        if not frames:
            return pd.DataFrame()

        if join_mode == "exact":
            result = _exact_join(frames, spec.join_keys, spec.column_prefix)
        else:
            result = _asof_join(frames, spec.column_prefix)

    else:
        raise ValueError(f"Unknown join_mode {spec.join_mode!r}. Use 'concat', 'exact', or 'asof'.")

    # --- optional label join ------------------------------------------
    if spec.label_path is not None and spec.label_join_mode == "asof":
        result = _label_asof_join(result, Path(spec.label_path))

    return result


def _label_asof_join(features: pd.DataFrame, label_path: Path) -> pd.DataFrame:
    """Left asof join label Parquet onto the feature DataFrame by ts_event per instrument."""
    if not label_path.exists():
        return features
    labels = pd.read_parquet(label_path, engine="pyarrow")
    all_instruments = features["instrument_id"].unique() if "instrument_id" in features.columns else [None]
    parts: list[pd.DataFrame] = []
    for iid in all_instruments:
        f = features[features["instrument_id"] == iid].sort_values("ts_event").copy()
        l = labels
        if iid is not None and "instrument_id" in labels.columns:
            l = labels[labels["instrument_id"] == iid]
        l = l.sort_values("ts_event").copy()
        # Drop columns already in features (except ts_event)
        l_drop = [c for c in l.columns if c in f.columns and c != "ts_event"]
        l = l.drop(columns=l_drop, errors="ignore")
        merged = pd.merge_asof(f, l, on="ts_event", direction="forward")
        parts.append(merged)
    if not parts:
        return features
    return pd.concat(parts, ignore_index=True).sort_values("ts_event").reset_index(drop=True)


def load_feature_dataset_with_metadata(spec: FeatureDatasetSpec) -> FeatureDatasetResult:
    """Load feature dataset and return data together with provenance metadata.

    Returns a :class:`FeatureDatasetResult` containing the DataFrame and:
    - ``used_feature_sets`` — feature set IDs that contributed rows
    - ``row_count``, ``columns``, ``start``, ``end``

    Examples
    --------
    >>> result = load_feature_dataset_with_metadata(spec)
    >>> result.row_count
    8760
    >>> result.used_feature_sets
    ['vwm_features_v1', 'vol_features_v1']
    """
    df = load_feature_dataset(spec)

    if df.empty:
        return FeatureDatasetResult(
            data=df,
            used_feature_sets=[],
            row_count=0,
            columns=[],
            start=None,
            end=None,
        )

    # Determine which feature sets actually contributed
    if "feature_set_id" in df.columns:
        used = list(df["feature_set_id"].unique())
    else:
        # Prefixed join modes: infer from column prefixes
        used = []
        for fsid in spec.feature_set_ids:
            prefix = f"{fsid}__"
            if any(c.startswith(prefix) for c in df.columns):
                used.append(fsid)

    ts_col = df["ts_event"] if "ts_event" in df.columns else None
    return FeatureDatasetResult(
        data=df,
        used_feature_sets=used,
        row_count=len(df),
        columns=list(df.columns),
        start=int(ts_col.min()) if ts_col is not None and not ts_col.empty else None,
        end=int(ts_col.max()) if ts_col is not None and not ts_col.empty else None,
    )
