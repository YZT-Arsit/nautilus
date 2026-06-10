"""
FeatureEvent — first-class event type for computed feature data.

A FeatureEvent is the output of a FeatureEngine applied to one MarketEvent.
It is NOT a debug dict attached to SignalResult; it is a standalone, typed,
versionable data asset on par with market bar data.

Design rules
------------
- Immutable frozen dataclass; no Nautilus Cython dependencies.
- Online path: create one object per bar/tick — never a DataFrame.
- Offline path: batch-convert to DataFrame via to_row() / from_row().
- Training: filter is_warmup=False for point-in-time correctness.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)  # slots=True (Python 3.10+): ~30% less memory per object on hot path
class FeatureEvent:
    """A single computed feature snapshot tied to one market event.

    Parameters
    ----------
    ts_event : int
        Millisecond POSIX timestamp of the source market event.
    instrument_id : str
        E.g. ``"BTCUSDT-PERP.BINANCE"``.
    feature_set_id : str
        Stable identifier for this feature set, e.g. ``"vwm_features_v1"``.
    feature_version : str
        Schema version string, e.g. ``"1"``.
    values : dict[str, float | int | bool | str | None]
        Feature values keyed by feature name.  Each key must correspond to a
        ``FeatureFieldSpec`` in the matching ``FeatureSetSpec``.
    ts_init : int | None
        Wall-clock ms when the object was constructed; if None, equals ts_event.
    source_event_type : str | None
        Source event type, e.g. ``"bar"``, ``"trade_tick"``.
    source_event_ts : int | None
        ts_event of the source event (same as ts_event for bar feeds).
    is_warmup : bool
        True when produced during the warmup phase.  Training pipelines must
        exclude warmup events to preserve point-in-time correctness.
    metadata : dict | None
        Audit key-value pairs.  Not a feature column; stored as JSON in Parquet.
    """

    ts_event: int
    instrument_id: str
    feature_set_id: str
    feature_version: str
    values: dict[str, float | int | bool | str | None]
    ts_init: int | None = None
    source_event_type: str | None = None
    source_event_ts: int | None = None
    is_warmup: bool = False
    metadata: dict | None = None

    # ------------------------------------------------------------------
    # Offline serialisation helpers
    # ------------------------------------------------------------------

    def to_row(self) -> dict:
        """Flatten to a single dict for DataFrame / Parquet persistence.

        The online hot path must NOT call this per-event; buffer via
        OfflineFeatureStore instead and convert in bulk at flush time.
        """
        row: dict = {
            "ts_event": self.ts_event,
            "ts_init": self.ts_init if self.ts_init is not None else self.ts_event,
            "instrument_id": self.instrument_id,
            "feature_set_id": self.feature_set_id,
            "feature_version": self.feature_version,
            "is_warmup": self.is_warmup,
            "source_event_type": self.source_event_type,
            "source_event_ts": self.source_event_ts,
        }
        row.update(self.values)
        if self.metadata:
            row["metadata_json"] = json.dumps(self.metadata, default=str)
        return row

    @classmethod
    def from_row(
        cls,
        row: dict,
        feature_columns: list[str],
    ) -> "FeatureEvent":
        """Reconstruct from a flat dict (e.g. a Parquet row).

        Parameters
        ----------
        row : dict
            A single row from a DataFrame or Arrow table.
        feature_columns : list[str]
            Names of the feature value columns — obtain from
            ``FeatureSetSpec.output_feature_names()``.
        """
        values = {k: row[k] for k in feature_columns if k in row}
        return cls(
            ts_event=int(row["ts_event"]),
            instrument_id=str(row["instrument_id"]),
            feature_set_id=str(row["feature_set_id"]),
            feature_version=str(row["feature_version"]),
            values=values,
            ts_init=int(row["ts_init"]) if row.get("ts_init") is not None else None,
            source_event_type=row.get("source_event_type"),
            source_event_ts=(
                int(row["source_event_ts"])
                if row.get("source_event_ts") is not None
                else None
            ),
            is_warmup=bool(row.get("is_warmup", False)),
            metadata=(
                json.loads(row["metadata_json"])
                if row.get("metadata_json")
                else None
            ),
        )
