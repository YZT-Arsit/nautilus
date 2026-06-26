"""Declarative adapter registry for VWM-compatible data preparation."""
from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from research.data_type_adapters import CANONICAL_SCHEMAS


@dataclass(frozen=True)
class AdapterSpec:
    raw_type: str
    adapter_name: str
    input_schema: str
    output_type: str
    output_schema: str
    supported_strategy: tuple[str, ...]
    vwm_compatibility: str
    confidence_level: str
    caveat: str
    implemented_status: str
    implementation_ref: str
    metadata_source: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SupportMatrixRow:
    raw_data_type: str
    current_source: str
    current_symbols: str
    direct_vwm_compatible: str
    adapter_required: str
    adapter_name: str
    output_type: str
    reliability: str
    caveat: str
    current_status: str
    next_action: str

    def to_markdown_row(self) -> str:
        values = (
            self.raw_data_type,
            self.current_source,
            self.current_symbols,
            self.direct_vwm_compatible,
            self.adapter_required,
            self.adapter_name,
            self.output_type,
            self.reliability,
            self.caveat,
            self.current_status,
            self.next_action,
        )
        return "| " + " | ".join(_escape_markdown(value) for value in values) + " |"


ADAPTER_REGISTRY: tuple[AdapterSpec, ...] = (
    AdapterSpec(
        raw_type="ohlcv_bar",
        adapter_name="direct_bar_adapter",
        input_schema="canonical OHLCV bar columns",
        output_type="canonical_trade_bar",
        output_schema="canonical_trade_bar",
        supported_strategy=("vwm",),
        vwm_compatibility="true",
        confidence_level="high",
        caveat="real traded OHLCV",
        implemented_status="available",
        implementation_ref="scripts.run_vwm_batch_backtests inventory path",
    ),
    AdapterSpec(
        raw_type="aggTrades",
        adapter_name="aggtrades_to_ohlcv_bar",
        input_schema="price, quantity, quote_quantity, count, timestamp",
        output_type="canonical_trade_bar",
        output_schema="canonical_trade_bar",
        supported_strategy=("vwm",),
        vwm_compatibility="true",
        confidence_level="high",
        caveat="real traded OHLCV after aggregation; coverage still date-dependent",
        implemented_status="planned",
        implementation_ref="data_engine transforms or future research adapter",
    ),
    AdapterSpec(
        raw_type="trade_tick",
        adapter_name="trades_to_ohlcv_bar",
        input_schema="price, quantity, quote_quantity, timestamp",
        output_type="canonical_trade_bar",
        output_schema="canonical_trade_bar",
        supported_strategy=("vwm",),
        vwm_compatibility="true",
        confidence_level="high",
        caveat="real traded OHLCV after aggregation; coverage still date-dependent",
        implemented_status="planned",
        implementation_ref="data_engine transforms or future research adapter",
    ),
    AdapterSpec(
        raw_type="quote_tick",
        adapter_name="quote_tick_to_mid_bar",
        input_schema="bid_price, ask_price, timestamp",
        output_type="canonical_mid_bar",
        output_schema="canonical_mid_bar",
        supported_strategy=("vwm",),
        vwm_compatibility="smoke_only",
        confidence_level="medium",
        caveat="derived mid-price bar; not trade OHLCV; volume is quote update count or zero",
        implemented_status="available",
        implementation_ref="research.cffex_bar_converter.quote_rows_to_mid_bars",
    ),
    AdapterSpec(
        raw_type="order_book_depth",
        adapter_name="depth_to_mid_bar",
        input_schema="top bid, top ask, timestamp",
        output_type="canonical_mid_bar",
        output_schema="canonical_mid_bar",
        supported_strategy=("vwm",),
        vwm_compatibility="smoke_only",
        confidence_level="medium",
        caveat="derived top-of-book mid-price bar; not trade OHLCV; can also produce depth features",
        implemented_status="available",
        implementation_ref="research.cffex_bar_converter.depth_rows_to_mid_bars",
    ),
    AdapterSpec(
        raw_type="futures_contract",
        adapter_name="contract_to_instrument_metadata",
        input_schema="native contract definition or deterministic MVP mapping fields",
        output_type="canonical_instrument_metadata",
        output_schema="canonical_instrument_metadata",
        supported_strategy=("vwm",),
        vwm_compatibility="metadata_only",
        confidence_level="high if native_catalog, medium if deterministic_mvp",
        caveat="metadata only; deterministic_mvp source must remain marked until catalog-backed",
        implemented_status="partial",
        implementation_ref="strategy_framework.backends.nautilus_native.resolve_instrument_mapping",
        metadata_source="deterministic_mvp",
    ),
)

SUPPORT_MATRIX_ROWS: tuple[SupportMatrixRow, ...] = (
    SupportMatrixRow(
        raw_data_type="OHLCV bar",
        current_source="Hive historical_data/market_data",
        current_symbols="BTCUSDT",
        direct_vwm_compatible="yes",
        adapter_required="no",
        adapter_name="direct_bar_adapter",
        output_type="canonical_trade_bar",
        reliability="high",
        caveat="real traded OHLCV",
        current_status="usable for BTCUSDT 1m/5m smoke",
        next_action="expand inventory and report packaging",
    ),
    SupportMatrixRow(
        raw_data_type="quote_tick",
        current_source="CFFEX native catalog",
        current_symbols="IC/IF/IH/IM 2301/2302/2303/2306",
        direct_vwm_compatible="no",
        adapter_required="yes",
        adapter_name="quote_tick_to_mid_bar",
        output_type="canonical_mid_bar",
        reliability="medium",
        caveat="not trade OHLCV; smoke-only strategy evidence",
        current_status="small IF2303 conversion smoke passed",
        next_action="C2d multi-contract derived conversion smoke",
    ),
    SupportMatrixRow(
        raw_data_type="order_book_depth",
        current_source="CFFEX native catalog",
        current_symbols="IC/IF/IH/IM 2301/2302/2303/2306",
        direct_vwm_compatible="no",
        adapter_required="yes",
        adapter_name="depth_to_mid_bar / depth_to_features",
        output_type="canonical_mid_bar / feature_frame",
        reliability="medium",
        caveat="not trade OHLCV; top-of-book mid or derived book features",
        current_status="synthetic converter tests only",
        next_action="design feature-frame path before strategy use",
    ),
    SupportMatrixRow(
        raw_data_type="futures_contract",
        current_source="CFFEX native catalog",
        current_symbols="IC/IF/IH/IM 2301/2302/2303/2306",
        direct_vwm_compatible="no",
        adapter_required="yes",
        adapter_name="contract_to_instrument_metadata",
        output_type="canonical_instrument_metadata",
        reliability="high if catalog-backed; current MVP deterministic",
        caveat="metadata only; does not enter VWM data feed",
        current_status="deterministic MVP mapping tests passed",
        next_action="replace MVP fields with catalog-backed metadata",
    ),
    SupportMatrixRow(
        raw_data_type="aggTrades / trade ticks",
        current_source="sparse BTCUSDT aggTrades",
        current_symbols="BTCUSDT",
        direct_vwm_compatible="no",
        adapter_required="yes",
        adapter_name="trades_to_ohlcv_bar",
        output_type="canonical_trade_bar",
        reliability="high",
        caveat="coverage insufficient for train/validation until date coverage is checked",
        current_status="planned registry entry",
        next_action="add coverage-aware trade-to-bar conversion smoke",
    ),
)


def get_adapter(raw_type: str) -> AdapterSpec:
    for spec in ADAPTER_REGISTRY:
        if spec.raw_type == raw_type:
            return spec
    raise KeyError(f"unknown adapter raw_type: {raw_type}")


def adapters_for_output(output_type: str) -> tuple[AdapterSpec, ...]:
    return tuple(spec for spec in ADAPTER_REGISTRY if spec.output_type == output_type)


def validate_registry() -> None:
    for spec in ADAPTER_REGISTRY:
        if spec.output_schema not in CANONICAL_SCHEMAS:
            raise ValueError(f"{spec.raw_type} references unknown schema {spec.output_schema}")
        if spec.output_type != spec.output_schema:
            raise ValueError(f"{spec.raw_type} output_type and output_schema differ")
        if spec.vwm_compatibility in {"smoke_only", "metadata_only"} and not spec.caveat:
            raise ValueError(f"{spec.raw_type} requires caveat")


def render_support_matrix_markdown(rows: tuple[SupportMatrixRow, ...] = SUPPORT_MATRIX_ROWS) -> str:
    header = (
        "| raw_data_type | current_source | current_symbols | direct_vwm_compatible | "
        "adapter_required | adapter_name | output_type | reliability | caveat | "
        "current_status | next_action |"
    )
    divider = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    return "\n".join([header, divider, *(row.to_markdown_row() for row in rows)])


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|")
