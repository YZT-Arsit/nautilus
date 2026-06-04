"""
ModelInferenceContext — prepare feature vectors for model inference.

Reads the latest FeatureEvents from OnlineFeatureStore and assembles a
flat feature vector suitable for model prediction.

No ML framework is required.  The actual model is supplied by the user
and called outside this module.  This class only handles the data
plumbing between the Feature Data Layer and the model input interface.

Missing feature policy
    When a feature key is requested but not present in the store, the
    behavior is controlled by ``missing_feature_policy``:

    - ``"fill_none"`` (default) — missing values become None.
    - ``"fill_zero"``           — missing values become 0.0.
    - ``"raise"``               — raises ValueError listing missing keys.

Output formats
    ``get_feature_vector``  — dict[str, Any]     (default, all frameworks)
    ``get_feature_list``    — list[float | None] (sklearn, XGBoost, ...)
    ``get_feature_array``   — numpy ndarray or list[float] if numpy absent

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

_VALID_POLICIES = frozenset({"fill_none", "fill_zero", "raise"})


@dataclass
class ModelInferenceContext:
    """Assembles model input from live feature snapshots.

    Parameters
    ----------
    online_store : OnlineFeatureStore
        Source of the latest feature snapshots.
    feature_set_ids : list[str]
        Feature sets to include in the vector, in the order they are read.
        All feature sets are queried from OnlineFeatureStore; none are read
        from Parquet on this path.
    feature_order : list[str] | None
        If provided, the output vector / list uses this exact key order.
        Keys must be in ``"{feature_set_id}.{feature_name}"`` format.
        If a key is absent from the store, the missing_feature_policy applies.
        If None, the order is feature_set_id insertion order + values dict
        iteration order.
    missing_feature_policy : str
        One of ``"fill_none"`` (default), ``"fill_zero"``, ``"raise"``.
        Controls what happens when a requested feature key is unavailable.
    """

    online_store: OnlineFeatureStore
    feature_set_ids: list[str]
    feature_order: list[str] | None = None
    missing_feature_policy: str = "fill_none"

    def __post_init__(self) -> None:
        if self.missing_feature_policy not in _VALID_POLICIES:
            raise ValueError(
                f"missing_feature_policy must be one of {sorted(_VALID_POLICIES)}, "
                f"got {self.missing_feature_policy!r}"
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _raw_vector(self, instrument_id: str) -> dict[str, Any]:
        """Build the raw feature dict before applying feature_order / policy."""
        vector: dict[str, Any] = {}
        for feature_set_id in self.feature_set_ids:
            fe = self.online_store.get_latest(instrument_id, feature_set_id)
            if fe is not None:
                for k, v in fe.values.items():
                    vector[f"{feature_set_id}.{k}"] = v
        return vector

    def _apply_order_and_policy(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Apply feature_order and missing_feature_policy to a raw vector."""
        if self.feature_order is None:
            ordered = raw
        else:
            ordered = {k: raw.get(k) for k in self.feature_order}

        policy = self.missing_feature_policy
        if policy == "fill_none":
            return ordered
        if policy == "fill_zero":
            return {k: (0.0 if v is None else v) for k, v in ordered.items()}
        # "raise"
        missing = [k for k, v in ordered.items() if v is None]
        if missing:
            raise ValueError(f"ModelInferenceContext: missing features: {missing}")
        return ordered

    # ------------------------------------------------------------------
    # Public API — output formats
    # ------------------------------------------------------------------

    def get_feature_vector(self, instrument_id: str) -> dict[str, Any]:
        """Return a flat dict ``"{feature_set_id}.{name}"`` → value.

        Returns an empty dict if no features are available.

        Examples
        --------
        >>> ctx = ModelInferenceContext(store, ["vwm_features_v1"])
        >>> vec = ctx.get_feature_vector("BTCUSDT-PERP.BINANCE")
        >>> vec["vwm_features_v1.vwm"]
        0.042
        """
        raw = self._raw_vector(instrument_id)
        return self._apply_order_and_policy(raw)

    def get_feature_list(self, instrument_id: str) -> list:
        """Return feature values as a list.

        Order follows ``feature_order`` when set, otherwise insertion order.
        Missing values follow ``missing_feature_policy`` (None / 0.0 / raise).

        Returns
        -------
        list[float | None]
            One element per feature in the resolved order.
        """
        vec = self.get_feature_vector(instrument_id)
        return list(vec.values())

    def get_feature_array(self, instrument_id: str):
        """Return feature values as a numpy array (float64).

        Falls back to :meth:`get_feature_list` when numpy is not installed.

        Returns
        -------
        numpy.ndarray or list
            Feature values in resolved order.
        """
        lst = self.get_feature_list(instrument_id)
        try:
            import numpy as np  # type: ignore[import]
            return np.array(lst, dtype=float)
        except ImportError:
            return lst

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    def get_latest_events(
        self,
        instrument_id: str,
    ) -> dict[str, FeatureEvent | None]:
        """Return the latest FeatureEvent per feature_set_id.

        Returns None for any feature set that has no recorded event yet.
        """
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
