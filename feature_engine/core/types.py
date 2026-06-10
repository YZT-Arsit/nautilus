"""Common type aliases used across the framework.

We use Polars as the primary in-memory representation because it is Arrow-backed,
multi-threaded, and zero-copy convertible to pyarrow.Table. Where a function is
genuinely format-agnostic we accept either ``pl.DataFrame`` or ``pa.Table``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, Union

if TYPE_CHECKING:
    import polars as pl
    import pyarrow as pa

    Frame: TypeAlias = Union["pl.DataFrame", "pa.Table"]
    Batch: TypeAlias = "pl.DataFrame"  # canonical micro-batch type for Feature.update
else:
    Frame = "Frame"
    Batch = "Batch"


# Canonical column names. Centralised so renames stay in one place.
class Cols:
    SYMBOL = "symbol"
    TS_EVENT = "ts_event"
    TS_INIT = "ts_init"
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"
    TURNOVER = "turnover"
    BID = "bid"
    ASK = "ask"
    TRADING_DATE = "trading_date"
    EXCHANGE = "exchange"
    FREQUENCY = "frequency"
    ASSET_CLASS = "asset_class"
