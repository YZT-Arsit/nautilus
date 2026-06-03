"""
VwmBarFeatureEngine — Feature Data Layer adapter for VwmFeatureEngine.

Wraps the existing ``VwmFeatureEngine`` (which outputs ``VwmFeatureSnapshot``)
and adapts it to the ``BaseFeatureEngine`` protocol, emitting ``FeatureEvent``
objects instead.

Registered as ``"vwm_features_v1"`` in the feature registry so it can be
built from configuration::

    engine = build_feature_engine("vwm_features_v1")
    engine = build_feature_engine("vwm_features_v1", params={"mom_len": 3})

Backward compatibility
    The original ``VwmFeatureEngine`` is preserved unchanged.  The existing
    ``VolumeWeightedMomentumShortSignalEngine`` continues to use it directly
    (Mode A) — this adapter is only needed for Mode B (feature-externalised)
    strategies and for writing features to the FeatureStore.

Nautilus dependency note
    ``VwmFeatureEngine`` internally uses ``EmaFeature`` / ``AtrFeature`` which
    wrap Nautilus Cython indicators.  If Nautilus is not compiled, importing
    this module raises ``ImportError``.  Tests that use this adapter should be
    marked with ``@pytest.mark.nautilus_required``.
"""
from __future__ import annotations

from nautilus_ext.features.feature_engine import FeatureEngineBase
from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_registry import register_feature_engine
from nautilus_ext.features.feature_schema import FeatureFieldSpec, FeatureSetSpec
from nautilus_ext.features.vwm_features import VwmFeatureConfig, VwmFeatureEngine
from nautilus_ext.strategies.interfaces.input_types import BarInput

# ------------------------------------------------------------------
# Schema definition
# ------------------------------------------------------------------

VWM_FEATURE_SCHEMA_V1 = FeatureSetSpec(
    feature_set_id="vwm_features_v1",
    version="1",
    input_types=["bar"],
    output_features=[
        FeatureFieldSpec(
            "current_bar", "int", nullable=False,
            description="Bar counter since engine reset / warmup.",
        ),
        FeatureFieldSpec(
            "momentum", "float", nullable=True,
            description="Raw momentum: close[t] - close[t - mom_len].",
        ),
        FeatureFieldSpec(
            "vwm", "float", nullable=True,
            description="Volume-weighted momentum EMA(avg_len).",
        ),
        FeatureFieldSpec(
            "atr", "float", nullable=True,
            description="Average True Range EMA(atr_len).",
        ),
        FeatureFieldSpec(
            "prev_vwm", "float", nullable=True,
            description="VWM from the previous bar.",
        ),
        FeatureFieldSpec(
            "prev_atr", "float", nullable=True,
            description="ATR from the previous bar.",
        ),
        FeatureFieldSpec(
            "bull_setup", "bool", nullable=False,
            description="True when VWM crosses over zero (upward).",
        ),
        FeatureFieldSpec(
            "bear_setup", "bool", nullable=False,
            description="True when VWM crosses under zero (downward).",
        ),
    ],
    required_history=20,   # default avg_len; first useful output at bar 20
    frequency=None,        # timeframe-agnostic
    point_in_time_safe=True,
    description=(
        "Volume-Weighted Momentum features: raw momentum, VWM EMA, ATR, "
        "and bull/bear setup crossover flags."
    ),
    owner="nautilus_ext",
)


# ------------------------------------------------------------------
# Engine
# ------------------------------------------------------------------

@register_feature_engine("vwm_features_v1")
class VwmBarFeatureEngine(FeatureEngineBase):
    """Feature Data Layer adapter wrapping VwmFeatureEngine.

    Accepts ``BarInput`` events and returns a ``FeatureEvent`` with
    ``feature_set_id="vwm_features_v1"``.  Non-BarInput events return None.

    Parameters
    ----------
    config : VwmFeatureConfig | None
        If None, uses default VwmFeatureConfig().
    mom_len, avg_len, atr_len : int
        Convenience keyword alternatives to passing a full config object.
    """

    def __init__(
        self,
        config: VwmFeatureConfig | None = None,
        *,
        mom_len: int | None = None,
        avg_len: int | None = None,
        atr_len: int | None = None,
    ) -> None:
        if config is None:
            kwargs: dict = {}
            if mom_len is not None:
                kwargs["mom_len"] = mom_len
            if avg_len is not None:
                kwargs["avg_len"] = avg_len
            if atr_len is not None:
                kwargs["atr_len"] = atr_len
            config = VwmFeatureConfig(**kwargs)
        self._config = config
        self._engine = VwmFeatureEngine(config)

    # ------------------------------------------------------------------
    # BaseFeatureEngine protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "vwm_features_v1"

    @property
    def schema(self) -> FeatureSetSpec:
        return VWM_FEATURE_SCHEMA_V1

    def reset(self) -> None:
        self._engine.reset()

    def update(self, event) -> FeatureEvent | None:
        """Accept BarInput; return FeatureEvent.  Ignore other event types."""
        if not isinstance(event, BarInput):
            return None
        snap = self._engine.update(event)
        ts = event.ts_event if event.ts_event is not None else 0
        instrument_id = event.instrument_id or ""
        return FeatureEvent(
            ts_event=ts,
            instrument_id=instrument_id,
            feature_set_id="vwm_features_v1",
            feature_version="1",
            values={
                "current_bar": snap.current_bar,
                "momentum": snap.momentum,
                "vwm": snap.vwm,
                "atr": snap.atr,
                "prev_vwm": snap.prev_vwm,
                "prev_atr": snap.prev_atr,
                "bull_setup": snap.bull_setup,
                "bear_setup": snap.bear_setup,
            },
            source_event_type="bar",
            source_event_ts=ts,
        )

    def state_dict(self) -> dict:
        return self._engine.state_dict()

    def load_state_dict(self, state: dict) -> None:
        self._engine.load_state_dict(state)
