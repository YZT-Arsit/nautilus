"""Demo / example helpers for feature_engine.

Includes the feature-compute demo helpers (``synthetic_bars``) moved here from
``nautilus_ext.features.examples``, alongside the offline/streaming examples
(``daily_update``, ``offline_backfill``, ``streaming_sim``).
"""
from feature_engine.examples.synthetic_bars import BarEvent, make_bars

__all__ = ["BarEvent", "make_bars"]
