"""Concrete strategy definitions.

Each module here declares one strategy: the features it needs (``build_specs``),
a config dataclass, and a strategy class with ``on_snapshot``. Modules use only
the public API (:mod:`nautilus_ext.features.api`) and never the compute layer.

To make a strategy runnable by the shared runner, register it in
:mod:`feature_strategies.registry`.
"""
