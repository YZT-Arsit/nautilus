"""
FeatureJoiner — join FeatureEvents with bar / tick DataFrames.

Provides two joining modes:

1. ``join_df`` — offline batch join of a bars DataFrame with a features DataFrame
   on a shared timestamp column.
2. ``join_latest`` — online point-in-time join: for a given bar timestamp, fetch
   the most recent FeatureEvent from the OnlineFeatureStore.

These helpers are used by training dataset builders and by the runner's CSV
export logic to attach feature values to bar rows.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from nautilus_ext.features.feature_store import OnlineFeatureStore


class FeatureJoiner:
    """Utility class for joining feature data with market data."""

    @staticmethod
    def join_df(
        bars_df: pd.DataFrame,
        features_df: pd.DataFrame,
        on: str = "ts_event",
        suffixes: tuple[str, str] = ("", "_feat"),
    ) -> pd.DataFrame:
        """Left-join *features_df* onto *bars_df* by *on* column.

        Parameters
        ----------
        bars_df : pd.DataFrame
            Base DataFrame (e.g. received_bars.csv).
        features_df : pd.DataFrame
            Feature DataFrame from OfflineFeatureStore.query().
        on : str
            Join column; default is ``"ts_event"``.
        suffixes : tuple[str, str]
            Suffixes for overlapping column names.

        Returns
        -------
        pd.DataFrame
            Bars enriched with feature columns.  Bars with no matching
            feature row get NaN in feature columns (left join semantics).
        """
        if features_df.empty or bars_df.empty:
            return bars_df.copy()

        # Drop system columns that shouldn't duplicate in the joined result
        drop_cols = [
            c for c in ["instrument_id", "feature_set_id", "feature_version",
                        "ts_init", "is_warmup", "source_event_type",
                        "source_event_ts"]
            if c in features_df.columns and c in bars_df.columns
        ]
        feat = features_df.drop(columns=drop_cols, errors="ignore")

        return bars_df.merge(feat, on=on, how="left", suffixes=suffixes)

    @staticmethod
    def join_latest(
        bar_ts: int,
        instrument_id: str,
        online_store: "OnlineFeatureStore",
        feature_set_ids: list[str],
    ) -> dict[str, Any]:
        """Fetch latest features for a bar from OnlineFeatureStore.

        Returns a flat dict with keys prefixed by feature_set_id::

            {"vwm_features_v1.momentum": 0.12, "vwm_features_v1.vwm": 0.07, ...}

        Only includes features with ts_event <= bar_ts (point-in-time safe).
        """
        result: dict[str, Any] = {}
        for fsid in feature_set_ids:
            fe = online_store.get_latest(instrument_id, fsid)
            if fe is not None and fe.ts_event <= bar_ts:
                for k, v in fe.values.items():
                    result[f"{fsid}.{k}"] = v
        return result

    @staticmethod
    def expand_feature_events(events: list) -> pd.DataFrame:
        """Convert a list of FeatureEvents to a DataFrame.

        Feature values are expanded into columns; system fields are preserved.
        """
        if not events:
            return pd.DataFrame()
        rows = [e.to_row() for e in events]
        return pd.DataFrame(rows)
