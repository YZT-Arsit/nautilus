"""Concrete feature implementations.

Importing this package registers all built-in features. Call :func:`load_all`
from worker entry points so process-pool / Ray children pick them up.
"""
from __future__ import annotations


def load_all() -> None:
    """Force-import every feature module so ``@register`` runs."""
    from quant_feature_engine.features import (  # noqa: F401, PLC0415
        derived,
        macd,
        moving_average,
        rolling_volatility,
        rsi,
        vwm,
    )


# Eager import for interactive use; workers call load_all() themselves.
load_all()
