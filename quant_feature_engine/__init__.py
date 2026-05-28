"""Unified feature engineering framework for offline backfill and streaming.

Importing this package is intentionally cheap: only the lightest sub-modules
are loaded. Anything that depends on polars / pyarrow is loaded lazily via
``__getattr__`` so that pure-Python utilities (e.g. ``storage.layout``,
``storage.metadata`` shape inspection) can be used without the heavy deps
installed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["Feature", "FeatureMeta", "register", "registry", "FeatureDAG"]


def __getattr__(name: str):  # PEP 562 lazy attribute access
    if name in {"Feature", "FeatureMeta"}:
        from quant_feature_engine.core.feature import Feature, FeatureMeta

        return {"Feature": Feature, "FeatureMeta": FeatureMeta}[name]
    if name == "register":
        from quant_feature_engine.core.registry import register

        return register
    if name == "registry":
        from quant_feature_engine.core.registry import registry

        return registry
    if name == "FeatureDAG":
        from quant_feature_engine.core.dag import FeatureDAG

        return FeatureDAG
    raise AttributeError(f"module 'quant_feature_engine' has no attribute {name!r}")


if TYPE_CHECKING:
    from quant_feature_engine.core.dag import FeatureDAG  # noqa: F401
    from quant_feature_engine.core.feature import Feature, FeatureMeta  # noqa: F401
    from quant_feature_engine.core.registry import register, registry  # noqa: F401
