"""
FeatureDataset — load historical feature data for model training.

This module provides the read interface between the Feature Data Layer
(OfflineFeatureStore) and a training pipeline.  No model is trained here;
the output is a plain pandas DataFrame that can be consumed by any ML library.

Point-in-time correctness
    By default, ``load_feature_dataset`` excludes warmup rows (is_warmup=True).
    This prevents look-ahead bias: features computed during the warmup phase
    use data that would not be available at prediction time in production.

Future integration
    Once a model registry exists, ``FeatureDatasetSpec`` will be extended with
    a ``label_spec`` field describing how to join feature rows with labels.
    For now the spec only describes the feature side.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from nautilus_ext.features.feature_store import OfflineFeatureStore


@dataclass
class FeatureDatasetSpec:
    """Specification for loading a training / evaluation feature dataset.

    Parameters
    ----------
    feature_store_path : str | Path
        Root path of the OfflineFeatureStore.
    feature_set_ids : list[str]
        One or more feature set identifiers to include (e.g. ``["vwm_features_v1"]``).
    instruments : list[str] | None
        If None, returns all instruments in the store.
    start : int | None
        Millisecond POSIX timestamp lower bound (inclusive).
    end : int | None
        Millisecond POSIX timestamp upper bound (inclusive).
    include_warmup : bool
        If True, include rows flagged as warmup.  Default False preserves
        point-in-time correctness.
    """

    feature_store_path: str | Path
    feature_set_ids: list[str]
    instruments: list[str] | None = None
    start: int | None = None
    end: int | None = None
    include_warmup: bool = False
    select_columns: list[str] | None = None  # None = all columns; required metadata always included


def load_feature_dataset(spec: FeatureDatasetSpec) -> pd.DataFrame:
    """Load feature data from OfflineFeatureStore as a training DataFrame.

    Returns a pandas DataFrame with columns:
    - ``ts_event``, ``instrument_id``, ``feature_set_id``, ``feature_version``
    - All feature columns (NaN where not available)
    - ``is_warmup`` (False unless ``spec.include_warmup=True``)

    Multi-instrument and multi-feature-set results are concatenated and
    sorted by ``ts_event``.

    Examples
    --------
    >>> spec = FeatureDatasetSpec(
    ...     feature_store_path="outputs/features",
    ...     feature_set_ids=["vwm_features_v1"],
    ...     instruments=["BTCUSDT-PERP_BINANCE"],
    ...     start=1_700_000_000_000,
    ...     end=1_701_000_000_000,
    ... )
    >>> df = load_feature_dataset(spec)
    >>> df.columns.tolist()
    ['ts_event', 'instrument_id', ..., 'momentum', 'vwm', 'atr', ...]
    """
    store = OfflineFeatureStore(spec.feature_store_path)
    parts: list[pd.DataFrame] = []

    for feature_set_id in spec.feature_set_ids:
        if spec.instruments:
            for instrument_id in spec.instruments:
                df = store.query(
                    instrument_id=instrument_id,
                    feature_set_id=feature_set_id,
                    start=spec.start,
                    end=spec.end,
                    include_warmup=spec.include_warmup,
                )
                if not df.empty:
                    parts.append(df)
        else:
            df = store.query(
                feature_set_id=feature_set_id,
                start=spec.start,
                end=spec.end,
                include_warmup=spec.include_warmup,
            )
            if not df.empty:
                parts.append(df)

    if not parts:
        return pd.DataFrame()

    result = pd.concat(parts, ignore_index=True)
    result = result.sort_values("ts_event").reset_index(drop=True)

    # Apply column selection; required metadata columns are always kept
    if spec.select_columns is not None:
        _required = {
            "ts_event", "instrument_id", "feature_set_id",
            "feature_version", "is_warmup",
        }
        keep = list(_required | set(spec.select_columns))
        keep = [c for c in keep if c in result.columns]
        result = result[keep]

    return result
