"""Canonical data schemas for the multi-data-type adapter layer.

This module is intentionally declarative. VWM continues to consume canonical
bars only; raw data sources are classified here before an adapter/converter is
chosen elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalSchema:
    name: str
    required_fields: tuple[str, ...]
    bar_source_values: tuple[str, ...]
    caveat: str


@dataclass(frozen=True)
class DataTypeLayer:
    name: str
    raw_types: tuple[str, ...]
    purpose: str


CANONICAL_TRADE_BAR_FIELDS = (
    "ts",
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "source",
    "bar_source",
    "ingested_at",
)

CANONICAL_MID_BAR_FIELDS = (
    "ts",
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "source",
    "bar_source",
    "volume_policy",
    "is_trade_bar",
    "ingested_at",
)

CANONICAL_INSTRUMENT_METADATA_FIELDS = (
    "instrument_id",
    "exchange",
    "venue_type",
    "symbol",
    "asset_class",
    "tick_size",
    "lot_size",
    "multiplier",
    "currency",
    "expiry",
    "metadata_source",
    "caveat",
)

CANONICAL_SCHEMAS: dict[str, CanonicalSchema] = {
    "canonical_trade_bar": CanonicalSchema(
        name="canonical_trade_bar",
        required_fields=CANONICAL_TRADE_BAR_FIELDS,
        bar_source_values=("trade_bar",),
        caveat="real traded OHLCV",
    ),
    "canonical_mid_bar": CanonicalSchema(
        name="canonical_mid_bar",
        required_fields=CANONICAL_MID_BAR_FIELDS,
        bar_source_values=("quote_mid", "depth_mid"),
        caveat=(
            "derived price-path bar; not real traded OHLCV; strategy "
            "performance is pipeline smoke only unless trade bars exist"
        ),
    ),
    "canonical_instrument_metadata": CanonicalSchema(
        name="canonical_instrument_metadata",
        required_fields=CANONICAL_INSTRUMENT_METADATA_FIELDS,
        bar_source_values=(),
        caveat="metadata only; used for instrument mapping, not VWM data feed",
    ),
}

DATA_TYPE_LAYERS: tuple[DataTypeLayer, ...] = (
    DataTypeLayer(
        name="direct_backtest_data",
        raw_types=("ohlcv_bar",),
        purpose="Already canonical trade bars; can feed VWM directly.",
    ),
    DataTypeLayer(
        name="bar_derivable_data",
        raw_types=("aggTrades", "trade_tick", "quote_tick", "order_book_depth"),
        purpose="Raw market events that require an adapter before VWM.",
    ),
    DataTypeLayer(
        name="instrument_metadata",
        raw_types=("futures_contract", "instrument_definition"),
        purpose="Instrument facts for mapping, pricing, sizing, and fees.",
    ),
)

MID_BAR_VOLUME_POLICIES = ("quote_update_count", "depth_update_count", "zero", "unknown")
INSTRUMENT_METADATA_SOURCES = ("native_catalog", "deterministic_mvp", "manual_config")


def get_canonical_schema(name: str) -> CanonicalSchema:
    try:
        return CANONICAL_SCHEMAS[name]
    except KeyError as exc:
        raise KeyError(f"unknown canonical schema: {name}") from exc


def render_canonical_schemas_markdown() -> str:
    lines = ["| schema | required_fields | bar_source_values | caveat |"]
    lines.append("| --- | --- | --- | --- |")
    for schema in CANONICAL_SCHEMAS.values():
        bar_sources = ", ".join(schema.bar_source_values) if schema.bar_source_values else "n/a"
        lines.append(
            f"| {schema.name} | {', '.join(schema.required_fields)} | "
            f"{bar_sources} | {schema.caveat} |"
        )
    return "\n".join(lines)
