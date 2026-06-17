"""Shared base and helpers for the modular OHLCV feature library.

This package keeps newer technical features out of the historical, monolithic
``feature_engine/compute/features.py``.  To minimise migration risk, it **reuses**
the battle-tested scaffolding from that module rather than re-implementing it:

* ``_AbstractFeature`` — spec storage, event counting, trigger checking, cache,
  and the ``_emit`` / ``_no_change`` / ``_missing_field`` helpers,
* ``_field`` — duck-typed float field extraction,
* ``_ts_ns`` — timestamp selection per ``spec.trigger.time_semantics``.

Behaviour is therefore identical to the existing features.  No ``nautilus_trader``
import appears here or anywhere in the feature library; the maths references
standard technical-analysis definitions but is plain Python.
"""
from __future__ import annotations

import math  # noqa: F401  (re-exported for feature modules)
from typing import Any  # noqa: F401  (re-exported for feature modules)

# Reuse the internal scaffolding from the legacy module (same package).
from feature_engine.compute.features import (  # noqa: F401
    _AbstractFeature,
    _field,
    _ts_ns,
)
from feature_engine.compute.spec import (  # noqa: F401
    FeatureSpec,
    FeatureUpdate,
    FeatureValue,
    WarmupRequirement,
)
from feature_engine.compute.state import (  # noqa: F401
    RollingWindowState,
    VWAPState,
)

# Small constant guarding divisions (the spec's ``max(denom, eps)``).
_EPS = 1e-12

# window_unit -> nanoseconds, for time-based VWAP windows (matches VWAPFeature).
_NS_PER_UNIT: dict[str, int] = {
    "nanoseconds": 1,
    "milliseconds": 1_000_000,
    "seconds": 1_000_000_000,
    "minutes": 60_000_000_000,
}


def _bar_field(event: Any, name: str) -> float | None:
    """Alias of ``_field`` for readability in OHLCV features."""
    return _field(event, name)
