"""Market-type integration registry for VWM data-source expansion.

This layer sits above the raw data adapter registry. Market type determines
required metadata, sessions, fee assumptions, and tradability rules; raw data
type determines how files/events become canonical bars or metadata.
"""
from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from research.adapter_registry import ADAPTER_REGISTRY


@dataclass(frozen=True)
class MarketIntegrationSpec:
    market_type: str
    asset_class: str
    examples: tuple[str, ...]
    required_raw_data: tuple[str, ...]
    required_metadata: tuple[str, ...]
    canonical_data_output: tuple[str, ...]
    instrument_mapping_required: bool
    session_model: str
    fee_model_required: bool
    volume_semantics: str
    vwm_compatibility: str
    current_status: str
    caveat: str
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_markdown_row(self) -> str:
        values = (
            self.market_type,
            self.asset_class,
            ", ".join(self.examples),
            ", ".join(self.required_raw_data),
            ", ".join(self.required_metadata),
            ", ".join(self.canonical_data_output),
            self.vwm_compatibility,
            self.current_status,
            self.caveat,
            self.next_action,
        )
        return "| " + " | ".join(_escape_markdown(value) for value in values) + " |"


MARKET_INTEGRATION_REGISTRY: tuple[MarketIntegrationSpec, ...] = (
    MarketIntegrationSpec(
        market_type="crypto_spot",
        asset_class="crypto_spot",
        examples=("BTCUSDT.BINANCE", "ETHUSDT.BINANCE"),
        required_raw_data=("ohlcv_bar", "aggTrades", "trade_tick", "order_book_depth"),
        required_metadata=(
            "base_asset",
            "quote_asset",
            "price_precision",
            "size_precision",
            "fee_model",
            "24/7_session",
        ),
        canonical_data_output=("canonical_trade_bar", "canonical_instrument_metadata"),
        instrument_mapping_required=True,
        session_model="24/7",
        fee_model_required=True,
        volume_semantics="traded base quantity for trade bars",
        vwm_compatibility="true_if_trade_ohlcv_bar_exists",
        current_status="implemented / validated with BTCUSDT 1m and 5m smoke",
        caveat="aggTrades coverage must be checked before expanding trade-derived bars",
        next_action="add coverage-aware aggTrades/trade tick to OHLCV smoke",
    ),
    MarketIntegrationSpec(
        market_type="crypto_futures_or_perp",
        asset_class="crypto_derivative",
        examples=("BTCUSDT perpetual", "ETHUSDT perpetual"),
        required_raw_data=("ohlcv_bar", "trade_tick", "funding_rate", "mark_price", "index_price"),
        required_metadata=(
            "contract_type",
            "multiplier",
            "margin_currency",
            "funding_schedule",
            "fee_model",
        ),
        canonical_data_output=(
            "canonical_trade_bar",
            "canonical_instrument_metadata",
            "funding_metadata_future",
        ),
        instrument_mapping_required=True,
        session_model="24/7 derivative session",
        fee_model_required=True,
        volume_semantics="contract or base quantity depending on venue",
        vwm_compatibility="true_for_trade_bars_caveated_if_funding_ignored",
        current_status="planned unless existing data is inventoried",
        caveat="funding, mark price, and margin effects are not represented in simple spot-style bars",
        next_action="inventory available perp bars and funding/mark metadata",
    ),
    MarketIntegrationSpec(
        market_type="equity_index_futures",
        asset_class="futures",
        examples=("IF2303.CFFEX", "IH2303.CFFEX", "IC2303.CFFEX", "IM2303.CFFEX"),
        required_raw_data=("quote_tick", "order_book_depth", "futures_contract", "trade_tick_if_available"),
        required_metadata=(
            "multiplier",
            "tick_size",
            "lot_size",
            "currency",
            "expiry",
            "exchange",
            "trading_session",
            "margin_model",
            "fee_model",
        ),
        canonical_data_output=(
            "canonical_mid_bar",
            "canonical_trade_bar_if_trade_ticks_available",
            "canonical_instrument_metadata",
        ),
        instrument_mapping_required=True,
        session_model="exchange trading sessions with day/night rules when applicable",
        fee_model_required=True,
        volume_semantics="quote update count for quote-mid smoke; traded volume only for trade bars",
        vwm_compatibility="smoke_only_for_quote_mid_true_only_for_real_trade_ohlcv",
        current_status="minimum pipeline validated with quote-mid derived bars and deterministic MVP mapping",
        caveat=(
            "CFFEX quote-mid bars are not real trade OHLCV; volume is quote update count; "
            "strategy results are pipeline-smoke evidence only"
        ),
        next_action="replace deterministic MVP mapping with catalog-backed futures_contract metadata",
    ),
    MarketIntegrationSpec(
        market_type="equities",
        asset_class="single_name_equity",
        examples=("A-share stock", "US stock", "single-name equity"),
        required_raw_data=("adjusted_ohlcv_bar", "raw_ohlcv_bar", "trade_tick", "corporate_actions"),
        required_metadata=(
            "exchange",
            "currency",
            "lot_size",
            "tick_size",
            "trading_calendar",
            "corporate_action_adjustment",
            "suspension_status",
            "limit_up_limit_down_rules",
        ),
        canonical_data_output=(
            "canonical_trade_bar",
            "canonical_adjusted_bar",
            "canonical_instrument_metadata",
        ),
        instrument_mapping_required=True,
        session_model="exchange calendar with holidays, suspensions, and intraday breaks",
        fee_model_required=True,
        volume_semantics="traded shares or lots, adjusted only when explicitly documented",
        vwm_compatibility="true_if_adjusted_or_trade_ohlcv_bar_exists",
        current_status="planned",
        caveat="corporate actions, suspensions, and limit rules must be handled before claiming comparability",
        next_action="define adjusted-bar policy and A-share/US-equity metadata requirements",
    ),
    MarketIntegrationSpec(
        market_type="etfs",
        asset_class="fund",
        examples=("index ETF", "sector ETF"),
        required_raw_data=("adjusted_ohlcv_bar", "nav_optional", "constituents_optional"),
        required_metadata=("exchange", "currency", "lot_size", "tick_size", "adjustment", "fee_model"),
        canonical_data_output=("canonical_trade_bar", "canonical_adjusted_bar", "canonical_instrument_metadata"),
        instrument_mapping_required=True,
        session_model="exchange calendar",
        fee_model_required=True,
        volume_semantics="traded shares or lots",
        vwm_compatibility="true_if_adjusted_ohlcv_bar_exists",
        current_status="planned",
        caveat="NAV and constituent data are optional for VWM but required for richer ETF analysis",
        next_action="inventory ETF adjusted bars and metadata availability",
    ),
    MarketIntegrationSpec(
        market_type="indices",
        asset_class="index",
        examples=("CSI300", "S&P500", "futures underlying index"),
        required_raw_data=("index_ohlc",),
        required_metadata=("currency", "index_provider", "session", "non_tradable_marker"),
        canonical_data_output=("canonical_index_bar",),
        instrument_mapping_required=False,
        session_model="index publication calendar",
        fee_model_required=False,
        volume_semantics="none or synthetic volume; not traded volume",
        vwm_compatibility="analysis_only",
        current_status="planned",
        caveat="indices are non-tradable unless mapped to a futures, ETF, or other tradable proxy",
        next_action="add non-tradable marker and proxy mapping policy",
    ),
    MarketIntegrationSpec(
        market_type="options",
        asset_class="option",
        examples=("option chain", "index option", "single-name option"),
        required_raw_data=("option_chain", "trade_tick", "quote_tick", "greeks", "implied_vol_surface"),
        required_metadata=("underlying", "strike", "expiry", "option_type", "multiplier", "exercise_style"),
        canonical_data_output=("option_chain_frame", "option_quote_frame", "option_metadata"),
        instrument_mapping_required=True,
        session_model="exchange option session",
        fee_model_required=True,
        volume_semantics="contracts traded; quote size for quote frames",
        vwm_compatibility="not_directly_compatible",
        current_status="future_work",
        caveat="requires option-specific strategy and risk model; VWM bar strategy is not directly applicable",
        next_action="plan option-specific data and strategy interface separately",
    ),
)

RAW_ADAPTER_MARKET_USAGE: dict[str, tuple[str, ...]] = {
    "ohlcv_bar": ("crypto_spot", "crypto_futures_or_perp"),
    "aggTrades": ("crypto_spot",),
    "trade_tick": ("crypto_spot", "crypto_futures_or_perp", "equities", "options"),
    "quote_tick": ("equity_index_futures", "options"),
    "order_book_depth": ("crypto_spot", "equity_index_futures"),
    "futures_contract": ("equity_index_futures",),
}


def get_market_integration(market_type: str) -> MarketIntegrationSpec:
    for spec in MARKET_INTEGRATION_REGISTRY:
        if spec.market_type == market_type:
            return spec
    raise KeyError(f"unknown market_type: {market_type}")


def infer_market_type(instrument_id: str) -> str:
    symbol = instrument_id.upper()
    if symbol.endswith(".CFFEX") and symbol[:2] in {"IF", "IH", "IC", "IM"}:
        return "equity_index_futures"
    if symbol.endswith(".BINANCE") or symbol in {"BTCUSDT", "ETHUSDT"}:
        return "crypto_spot"
    raise KeyError(f"cannot infer market_type for instrument_id: {instrument_id}")


def render_market_matrix_markdown(
    rows: tuple[MarketIntegrationSpec, ...] = MARKET_INTEGRATION_REGISTRY,
) -> str:
    header = (
        "| market_type | asset_class | examples | raw_data_available | metadata_available | "
        "canonical_output | vwm_compatibility | current_status | caveat | next_action |"
    )
    divider = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    return "\n".join([header, divider, *(row.to_markdown_row() for row in rows)])


def render_raw_adapter_market_matrix_markdown() -> str:
    header = (
        "| raw_data_type | adapter | output_type | trade_bar_or_mid_bar | confidence | "
        "caveat | used_by_market_types |"
    )
    divider = "| --- | --- | --- | --- | --- | --- | --- |"
    lines = [header, divider]
    for adapter in ADAPTER_REGISTRY:
        trade_bar_or_mid_bar = _classify_output(adapter.output_type)
        used_by = ", ".join(RAW_ADAPTER_MARKET_USAGE.get(adapter.raw_type, ()))
        lines.append(
            "| "
            + " | ".join(
                _escape_markdown(value)
                for value in (
                    adapter.raw_type,
                    adapter.adapter_name,
                    adapter.output_type,
                    trade_bar_or_mid_bar,
                    adapter.confidence_level,
                    adapter.caveat,
                    used_by,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def validate_market_registry() -> None:
    market_types = {spec.market_type for spec in MARKET_INTEGRATION_REGISTRY}
    if len(market_types) != len(MARKET_INTEGRATION_REGISTRY):
        raise ValueError("duplicate market_type in market integration registry")
    for raw_type, used_by in RAW_ADAPTER_MARKET_USAGE.items():
        if raw_type not in {adapter.raw_type for adapter in ADAPTER_REGISTRY}:
            raise ValueError(f"unknown raw adapter type in market usage: {raw_type}")
        missing = set(used_by) - market_types
        if missing:
            raise ValueError(f"{raw_type} references unknown market types: {sorted(missing)}")
    for spec in MARKET_INTEGRATION_REGISTRY:
        if spec.vwm_compatibility != "true_if_trade_ohlcv_bar_exists" and not spec.caveat:
            raise ValueError(f"{spec.market_type} requires a caveat")


def _classify_output(output_type: str) -> str:
    if output_type == "canonical_trade_bar":
        return "trade_bar"
    if output_type == "canonical_mid_bar":
        return "mid_bar"
    if output_type == "canonical_instrument_metadata":
        return "metadata"
    return "other"


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|")
