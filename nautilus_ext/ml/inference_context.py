"""
ModelInferenceContext — prepare feature vectors for model inference.

Reads the latest FeatureEvents from OnlineFeatureStore and assembles a
flat feature vector suitable for model prediction.

No ML framework is required.  The actual model is supplied by the user
and called outside this module.  This class only handles the data
plumbing between the Feature Data Layer and the model input interface.

Future integration
    - When a model registry exists, ModelInferenceContext will accept a
      ``model_id`` parameter and look up the expected feature order
      from the registry automatically.
    - When Nautilus TradingNode integration is added, this context will
      be created inside the live data engine callback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_store import OnlineFeatureStore


@dataclass
class ModelInferenceContext:
    """Assembles model input from live feature snapshots.

    Parameters
    ----------
    online_store : OnlineFeatureStore
        Source of the latest feature snapshots.
    feature_set_ids : list[str]
        Feature sets to include in the vector.
    feature_order : list[str] | None
        If provided, the output vector uses this exact key order.  Keys are
        in ``"{feature_set_id}.{feature_name}"`` format.  If a key is missing
        from the store the value is None.
        If None, the order is determined by feature_set_id + iteration order
        of ``values`` dict.
    """

    online_store: OnlineFeatureStore
    feature_set_ids: list[str]
    feature_order: list[str] | None = None

    def get_feature_vector(self, instrument_id: str) -> dict[str, Any]:
        """Return a flat dict of ``"{feature_set_id}.{name}"`` → value.

        Returns an empty dict if no features are available for this instrument.

        Examples
        --------
        >>> ctx = ModelInferenceContext(store, ["vwm_features_v1"])
        >>> vec = ctx.get_feature_vector("BTCUSDT-PERP.BINANCE")
        >>> vec["vwm_features_v1.vwm"]
        0.042
        """
        vector: dict[str, Any] = {}
        for feature_set_id in self.feature_set_ids:
            fe = self.online_store.get_latest(instrument_id, feature_set_id)
            if fe is not None:
                for k, v in fe.values.items():
                    vector[f"{feature_set_id}.{k}"] = v

        if self.feature_order is not None:
            vector = {k: vector.get(k) for k in self.feature_order}

        return vector

    def get_latest_events(
        self,
        instrument_id: str,
    ) -> dict[str, FeatureEvent | None]:
        """Return the latest FeatureEvent per feature_set_id."""
        return {
            fsid: self.online_store.get_latest(instrument_id, fsid)
            for fsid in self.feature_set_ids
        }

    def is_ready(self, instrument_id: str) -> bool:
        """Return True when all required feature sets have at least one event."""
        return all(
            self.online_store.get_latest(instrument_id, fsid) is not None
            for fsid in self.feature_set_ids
        )
