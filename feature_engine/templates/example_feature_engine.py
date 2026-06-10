"""
Feature Engine development template.

Copy this file and adapt it to build a new feature set.

Steps
-----
1. Choose a stable ``FEATURE_SET_ID`` (never rename after data is written).
2. Fill in ``MY_FEATURE_SCHEMA`` with the output columns your engine emits.
3. Implement ``update(event)`` — one FeatureEvent per supported event, None otherwise.
4. Implement ``state_dict`` / ``load_state_dict`` for checkpoint/restore.
5. Register with ``@register_feature_engine(FEATURE_SET_ID)``.
6. Wire the engine into a FeaturePipeline in your runner or demo.

Design rules (non-negotiable)
-------------------------------
- ``update()`` must NOT create a DataFrame.  One lightweight FeatureEvent per call.
- ``values`` must contain only feature data (floats, ints, bools, strs, None).
  Do NOT put metadata (instrument_id, ts_event) into values — those live in the
  FeatureEvent fields directly.
- ``is_warmup`` is stamped by FeaturePipeline, not by the engine.  The engine
  never sets it.
- ``feature_set_id`` in the returned FeatureEvent must match FEATURE_SET_ID.
- ``feature_version`` must match ``FeatureSetSpec.version``.  Bump both together
  when the output schema changes.
- Only set ``point_in_time_safe=True`` when the feature uses strictly information
  available at or before ts_event.  Never use future bars.
"""
from __future__ import annotations

from collections import deque

from feature_engine.feature_engine import FeatureEngineBase
from feature_engine.feature_event import FeatureEvent
from feature_engine.feature_registry import register_feature_engine
from feature_engine.feature_schema import FeatureFieldSpec, FeatureSetSpec
from nautilus_ext.strategies.interfaces.input_types import BarInput

# ---------------------------------------------------------------------------
# 1. Stable ID — must never change after data has been written to disk
# ---------------------------------------------------------------------------
FEATURE_SET_ID = "example_obv_v1"

# ---------------------------------------------------------------------------
# 2. Schema — documents every output column before any data is written
# ---------------------------------------------------------------------------
MY_FEATURE_SCHEMA = FeatureSetSpec(
    feature_set_id=FEATURE_SET_ID,
    version="1",
    input_types=["bar"],
    output_features=[
        FeatureFieldSpec(
            name="obv",
            dtype="float",
            nullable=True,
            description=(
                "On-Balance Volume: cumulative sum of volume * sign(close - prev_close). "
                "None until the second bar."
            ),
        ),
        FeatureFieldSpec(
            name="roc",
            dtype="float",
            nullable=True,
            description=(
                "Rate of Change over *window* bars: (close[t] / close[t-window] - 1) * 100. "
                "None until enough history."
            ),
        ),
        FeatureFieldSpec(
            name="bar_count",
            dtype="int",
            nullable=False,
            description="Bars processed since last reset.",
        ),
    ],
    required_history=20,   # roc needs window bars of history
    frequency=None,        # timeframe-agnostic
    point_in_time_safe=True,
    description="On-Balance Volume and Rate-of-Change (template example).",
    owner="team_quant",
)


# ---------------------------------------------------------------------------
# 3. Engine implementation
# ---------------------------------------------------------------------------

@register_feature_engine(FEATURE_SET_ID)
class ExampleObvEngine(FeatureEngineBase):
    """OBV + Rate-of-Change feature engine.

    Parameters
    ----------
    window : int
        Look-back window for ROC calculation.

    How to build from config (after registration):

        from feature_engine.feature_registry import build_feature_engine
        engine = build_feature_engine("example_obv_v1")
        engine = build_feature_engine("example_obv_v1", params={"window": 14})
    """

    def __init__(self, window: int = 20) -> None:
        self._window = window
        self._obv: float = 0.0
        self._prev_close: float | None = None
        self._closes: deque[float] = deque(maxlen=window + 1)
        self._bar_count: int = 0

    # ------------------------------------------------------------------
    # BaseFeatureEngine protocol — required properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return FEATURE_SET_ID

    @property
    def schema(self) -> FeatureSetSpec:
        return MY_FEATURE_SCHEMA

    # ------------------------------------------------------------------
    # State management — required for checkpoint / warm restart
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all state to initial condition (called before live session or test)."""
        self._obv = 0.0
        self._prev_close = None
        self._closes.clear()
        self._bar_count = 0

    def state_dict(self) -> dict:
        """Serialise state for checkpoint.  Must be JSON-serialisable."""
        return {
            "window": self._window,
            "obv": self._obv,
            "prev_close": self._prev_close,
            "closes": list(self._closes),
            "bar_count": self._bar_count,
        }

    def load_state_dict(self, state: dict) -> None:
        """Restore state from a checkpoint dict."""
        self._window = state["window"]
        self._obv = state["obv"]
        self._prev_close = state.get("prev_close")
        self._closes = deque(state["closes"], maxlen=self._window + 1)
        self._bar_count = state["bar_count"]

    # ------------------------------------------------------------------
    # Hot path — update() is called on every bar
    # ------------------------------------------------------------------

    def update(self, event) -> FeatureEvent | None:
        """Process one market event.

        - Returns ``None`` for event types other than BarInput.
        - Never creates a DataFrame.
        - Never sets ``is_warmup`` (FeaturePipeline stamps it).
        """
        # Ignore event types this engine does not handle
        if not isinstance(event, BarInput):
            return None

        close = float(event.close)
        volume = float(event.volume)
        self._bar_count += 1
        self._closes.append(close)

        # OBV update
        if self._prev_close is not None:
            if close > self._prev_close:
                self._obv += volume
            elif close < self._prev_close:
                self._obv -= volume
            # close == prev_close: OBV unchanged
        self._prev_close = close

        # ROC — needs at least window+1 data points
        roc: float | None = None
        if len(self._closes) == self._window + 1:
            closes = list(self._closes)
            denom = closes[0]
            if denom != 0.0:
                roc = (closes[-1] / denom - 1.0) * 100.0

        # OBV is None until we have at least 2 bars
        obv: float | None = self._obv if self._bar_count >= 2 else None

        return FeatureEvent(
            ts_event=event.ts_event,
            instrument_id=event.instrument_id,
            feature_set_id=FEATURE_SET_ID,
            feature_version="1",
            values={
                "obv": obv,
                "roc": roc,
                "bar_count": self._bar_count,
            },
            source_event_type="bar",
            source_event_ts=event.ts_event,
            # is_warmup is NOT set here — FeaturePipeline stamps it
        )
