"""Shared pytest fixtures and synthetic data generators."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl
import pytest


@pytest.fixture(scope="session", autouse=True)
def _register_features() -> None:
    """Ensure all built-in features are imported before any test runs."""
    from quant_feature_engine.features import load_all

    load_all()


@pytest.fixture
def synthetic_bars() -> pl.DataFrame:
    """Two symbols, 500 bars each, interleaved by event time.

    Designed to exercise per-symbol state in features by interleaving rows.
    """
    rng = np.random.default_rng(42)
    n = 500
    start = datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc)
    rows: list[dict] = []
    prices = {"AAA": 100.0, "BBB": 50.0}
    for i in range(n):
        ts = start + timedelta(minutes=i)
        for sym in ("AAA", "BBB"):
            ret = rng.normal(0, 0.001)
            prices[sym] *= 1 + ret
            close = prices[sym]
            rows.append(
                {
                    "symbol": sym,
                    "ts_event": ts,
                    "open": close * (1 - 0.0005),
                    "high": close * 1.001,
                    "low": close * 0.999,
                    "close": close,
                    "volume": float(rng.integers(1000, 10000)),
                    "turnover": close * 5000.0,
                }
            )
    return pl.DataFrame(rows).sort(["ts_event", "symbol"])
