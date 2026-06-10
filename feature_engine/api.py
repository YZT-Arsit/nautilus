"""Stable public API for feature-strategy authors.

Import the feature engine's user-facing types from **here**, not from the deep
``feature_engine.compute.*`` modules::

    from feature_engine.api import FeatureSpec, FeatureSnapshot

This module is the supported surface. The ``compute`` package underneath is the
low-level implementation (feature operators, backends, watermarks, …) and may
change without notice; this facade will not.

Exposed types
-------------
FeatureSpec
    Declarative description of one feature (name, input, window, params).
FeatureValue
    One feature's value + readiness at a point in time.
FeatureSnapshot
    All feature values for an instrument at one point in time; the object a
    strategy reads via ``snapshot.value(name)`` / ``snapshot.is_ready(name)``.
SpecFeatureEngine
    Spec-driven engine that turns events into snapshots. Most strategy code
    should prefer :class:`feature_engine.runner.FeatureStrategyRunner`,
    which wraps engine construction and the live loop.
rolling_mean_spec
    Convenience builder for a rolling-mean ``FeatureSpec`` (hides ``params``).
"""
from feature_engine.builders import rolling_mean_spec
from feature_engine.compute import (
    FeatureSnapshot,
    FeatureSpec,
    FeatureValue,
    SpecFeatureEngine,
)

__all__ = [
    "FeatureSpec",
    "FeatureValue",
    "FeatureSnapshot",
    "SpecFeatureEngine",
    "rolling_mean_spec",
]
