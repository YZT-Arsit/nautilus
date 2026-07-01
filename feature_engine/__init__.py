"""feature_engine — the self-built feature computation layer.

Public surface (import submodules explicitly; the package import is cheap):

* ``feature_engine.api``     — ``FeatureSpec`` / ``FeatureSnapshot`` + spec builders
                               (the stable, strategy-facing surface).
* ``feature_engine.runner``  — ``FeatureStrategyRunner`` (drive a strategy from specs).
* ``feature_engine.compute`` — the spec engine + the operator library
                               (``feature_engine.compute.feature_lib``).
* ``feature_engine.storage`` — Hive-parquet read/write for ``market_data`` and
                               ``feature_data`` (they are peers — "features are data").
* ``feature_engine.offline`` — ``HistoricalFeatureBuilder``: compute features from
                               historical bars and write them to ``feature_data``.

Architecture: ``data_engine -> feature_engine -> strategy_framework -> strategies``.

See ``docs/PLATFORM_ARCHITECTURE.md`` for the locked interfaces.
"""
from __future__ import annotations
