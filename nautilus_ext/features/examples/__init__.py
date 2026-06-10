"""Demo / example helpers for the feature-compute layer.

These utilities exist to keep runnable demos and tests short. They are not part
of the production hot path.
"""
from nautilus_ext.features.examples.synthetic_bars import BarEvent, make_bars

__all__ = ["BarEvent", "make_bars"]
