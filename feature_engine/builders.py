"""Convenience builders for common :class:`FeatureSpec` shapes.

These let strategy authors declare features without hand-writing the
``params={"type": ...}`` plumbing that the compute layer keys off. Import them
from the public facade::

    from feature_engine.api import rolling_mean_spec
"""
from __future__ import annotations

from feature_engine.compute import FeatureSpec


def rolling_mean_spec(
    name: str,
    *,
    input_type: str = "bar",
    input_field: str = "close",
    window: int,
) -> FeatureSpec:
    """Build a rolling-mean :class:`FeatureSpec`.

    Hides the ``params={"type": "rolling_mean"}`` detail so strategy code reads
    as intent ("a rolling mean of ``input_field`` over ``window``") rather than
    backend wiring.
    """
    return FeatureSpec(
        name,
        input_type=input_type,
        input_field=input_field,
        window=window,
        params={"type": "rolling_mean"},
    )
