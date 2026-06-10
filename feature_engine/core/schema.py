"""Canonical Arrow schemas for raw market data and computed features.

Keeping schemas centralised lets readers, writers, and the streaming adapter
validate the data shape at every boundary. Partition columns are *not* part of
the file schema — they are reconstructed from the Hive path by PyArrow Dataset.
"""
from __future__ import annotations

import pyarrow as pa

# ---------------------------------------------------------------------------
# Raw market data
# ---------------------------------------------------------------------------

BAR_SCHEMA: pa.Schema = pa.schema(
    [
        ("symbol", pa.dictionary(pa.int32(), pa.string())),
        ("ts_event", pa.timestamp("ns", tz="UTC")),
        ("ts_init", pa.timestamp("ns", tz="UTC")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("turnover", pa.float64()),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
    ]
)

TICK_SCHEMA: pa.Schema = pa.schema(
    [
        ("symbol", pa.dictionary(pa.int32(), pa.string())),
        ("ts_event", pa.timestamp("ns", tz="UTC")),
        ("ts_init", pa.timestamp("ns", tz="UTC")),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
    ]
)

# Partition columns (in path, not file). Order matters: it defines directory nesting.
RAW_PARTITIONS: tuple[str, ...] = ("asset_class", "exchange", "frequency", "trading_date")
FEATURE_PARTITIONS: tuple[str, ...] = ("feature_group", "frequency", "trading_date")


def feature_schema(feature_columns: list[tuple[str, pa.DataType]]) -> pa.Schema:
    """Build the schema for a feature partition file.

    Every feature file carries the join keys (``symbol``, ``ts_event``) plus
    one column per emitted feature. Partition columns are added by the writer.
    """
    fields: list[pa.Field] = [
        pa.field("symbol", pa.dictionary(pa.int32(), pa.string())),
        pa.field("ts_event", pa.timestamp("ns", tz="UTC")),
    ]
    fields.extend(pa.field(name, dtype) for name, dtype in feature_columns)
    return pa.schema(fields)
