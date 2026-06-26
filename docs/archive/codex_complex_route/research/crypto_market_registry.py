"""Crypto market registry for multi-symbol and futures expansion planning.

The registry is declarative: it records what the framework can represent and
what data has actually been observed. It does not fetch data or connect to
exchanges.
"""
from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass


@dataclass(frozen=True)
class CryptoMarketSpec:
    market_type: str
    exchange: str
    symbol_pattern: str
    instrument_id_pattern: str
    required_metadata: tuple[str, ...]
    optional_metadata: tuple[str, ...]
    canonical_data: tuple[str, ...]
    vwm_compatibility: str
    caveat: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CryptoSupportRow:
    exchange: str
    market_type: str
    symbol: str
    instrument_id: str
    raw_data_available: str
    bar_types_available: str
    trade_data_available: str
    funding_rate_available: str
    mark_price_available: str
    index_price_available: str
    instrument_metadata_available: str
    vwm_compatible: str
    current_status: str
    caveat: str
    next_action: str

    def to_markdown_row(self) -> str:
        values = (
            self.exchange,
            self.market_type,
            self.symbol,
            self.instrument_id,
            self.raw_data_available,
            self.bar_types_available,
            self.trade_data_available,
            self.funding_rate_available,
            self.mark_price_available,
            self.index_price_available,
            self.instrument_metadata_available,
            self.vwm_compatible,
            self.current_status,
            self.caveat,
            self.next_action,
        )
        return "| " + " | ".join(_escape_markdown(value) for value in values) + " |"


CRYPTO_MARKET_REGISTRY: tuple[CryptoMarketSpec, ...] = (
    CryptoMarketSpec(
        market_type="crypto_spot",
        exchange="BINANCE",
        symbol_pattern="{BASE}{QUOTE}",
        instrument_id_pattern="{SYMBOL}.BINANCE",
        required_metadata=("base_asset", "quote_asset", "tick_size", "lot_size", "fee_model"),
        optional_metadata=("price_precision", "size_precision", "order_book_depth"),
        canonical_data=("canonical_trade_bar", "canonical_trade_tick_bar"),
        vwm_compatibility="true_trade_bar",
        caveat="VWM is valid only when bars are real traded OHLCV, not quote or mark derived.",
        status="confirmed_available_for_BTCUSDT_spot_bars",
    ),
    CryptoMarketSpec(
        market_type="crypto_perpetual",
        exchange="BINANCE",
        symbol_pattern="{BASE}{QUOTE}",
        instrument_id_pattern="{SYMBOL}.BINANCE-PERP",
        required_metadata=(
            "contract_type",
            "base_asset",
            "quote_asset",
            "settlement_asset",
            "margin_asset",
            "contract_size",
            "tick_size",
            "lot_size",
            "fee_model",
        ),
        optional_metadata=(
            "exchange_info",
            "funding_rate",
            "funding_interval",
            "mark_price",
            "index_price",
            "liquidation_margin_metadata",
        ),
        canonical_data=(
            "canonical_trade_bar",
            "canonical_perpetual_instrument_metadata",
            "canonical_funding_rate",
            "canonical_mark_index_price",
        ),
        vwm_compatibility="true_trade_bar_with_funding_caveat",
        caveat="Funding, mark price, and margin effects must be explicit; ignored funding is a caveat.",
        status="funding_mark_index_smoke_validated_exchange_info_network_blocked",
    ),
    CryptoMarketSpec(
        market_type="crypto_delivery_futures",
        exchange="BINANCE",
        symbol_pattern="{BASE}{QUOTE}_{DELIVERY}",
        instrument_id_pattern="{SYMBOL}.BINANCE-DELIVERY",
        required_metadata=(
            "contract_type",
            "base_asset",
            "quote_asset",
            "settlement_asset",
            "delivery_date",
            "contract_size",
            "tick_size",
            "lot_size",
            "fee_model",
        ),
        optional_metadata=("mark_price", "index_price", "margin_metadata"),
        canonical_data=("canonical_trade_bar", "canonical_instrument_metadata"),
        vwm_compatibility="true_trade_bar_missing_metadata",
        caveat="Delivery futures require expiry/delivery metadata before cross-contract comparison.",
        status="adapter_code_exists_for_binance_futures_cm_but_no_local_delivery_data_confirmed",
    ),
)


CRYPTO_SUPPORT_ROWS: tuple[CryptoSupportRow, ...] = (
    CryptoSupportRow(
        exchange="BINANCE",
        market_type="crypto_spot",
        symbol="BTCUSDT",
        instrument_id="BTCUSDT.BINANCE",
        raw_data_available="OHLCV bars; aggTrades",
        bar_types_available="1m confirmed usable; 5m present but incomplete",
        trade_data_available="aggTrades confirmed: 33 date partitions, 2024-06-01..2026-06-16",
        funding_rate_available="no",
        mark_price_available="no",
        index_price_available="no",
        instrument_metadata_available="deterministic/TestInstrumentProvider mapping",
        vwm_compatible="true_trade_bar",
        current_status="confirmed_available",
        caveat="5m has date gaps; aggTrades coverage is sparse relative to bar history.",
        next_action="add ETHUSDT/BNBUSDT/SOLUSDT futures data import plan before claiming multi-symbol coverage",
    ),
    CryptoSupportRow(
        exchange="BINANCE",
        market_type="crypto_spot",
        symbol="ETHUSDT",
        instrument_id="ETHUSDT.BINANCE",
        raw_data_available="none confirmed in local/remote data tree",
        bar_types_available="none confirmed",
        trade_data_available="none confirmed",
        funding_rate_available="no",
        mark_price_available="no",
        index_price_available="no",
        instrument_metadata_available="deterministic/TestInstrumentProvider mapping exists in backend tests",
        vwm_compatible="planned",
        current_status="planned",
        caveat="code-level mapping exists, but no confirmed historical bars in scanned data tree",
        next_action="import or locate ETHUSDT spot/futures bars",
    ),
    CryptoSupportRow(
        exchange="BINANCE",
        market_type="crypto_perpetual",
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        raw_data_available="OHLCV bars",
        bar_types_available="5m confirmed for 2024-06-01",
        trade_data_available="kline trade_count available",
        funding_rate_available="smoke_validated",
        mark_price_available="smoke_validated",
        index_price_available="smoke_validated",
        instrument_metadata_available="exchange_info public REST audited but network-blocked; TestInstrumentProvider perpetual mapping",
        vwm_compatible="true_trade_bar_with_funding_caveat",
        current_status="e4_multisymbol_vwm_smoke_passed",
        caveat="Funding, liquidation, margin, and mark/index price effects are collected but not modeled in PnL",
        next_action="rerun exchange_info after REST endpoint is reachable, then add funding-aware evaluation",
    ),
    CryptoSupportRow(
        exchange="BINANCE",
        market_type="crypto_perpetual",
        symbol="ETHUSDT",
        instrument_id="ETHUSDT-PERP.BINANCE",
        raw_data_available="OHLCV bars",
        bar_types_available="5m confirmed for 2024-06-01",
        trade_data_available="kline trade_count available",
        funding_rate_available="smoke_validated",
        mark_price_available="smoke_validated",
        index_price_available="smoke_validated",
        instrument_metadata_available="exchange_info public REST audited but network-blocked; TestInstrumentProvider perpetual mapping",
        vwm_compatible="true_trade_bar_with_funding_caveat",
        current_status="e4_multisymbol_vwm_smoke_passed",
        caveat="Funding, liquidation, margin, and mark/index price effects are collected but not modeled in PnL",
        next_action="rerun exchange_info after REST endpoint is reachable, then add funding-aware evaluation",
    ),
    CryptoSupportRow(
        exchange="BINANCE",
        market_type="crypto_perpetual",
        symbol="SOLUSDT",
        instrument_id="SOLUSDT-PERP.BINANCE",
        raw_data_available="OHLCV bars",
        bar_types_available="5m confirmed for 2024-06-01",
        trade_data_available="kline trade_count available",
        funding_rate_available="planned",
        mark_price_available="planned",
        index_price_available="planned",
        instrument_metadata_available="deterministic MVP perpetual mapping",
        vwm_compatible="true_trade_bar_with_funding_caveat",
        current_status="e4_multisymbol_vwm_smoke_passed",
        caveat="Funding, liquidation, margin, and mark/index price effects are not modeled in PnL",
        next_action="add funding-aware evaluation before performance claims",
    ),
    CryptoSupportRow(
        exchange="BINANCE",
        market_type="crypto_perpetual",
        symbol="BNBUSDT",
        instrument_id="BNBUSDT-PERP.BINANCE",
        raw_data_available="OHLCV bars",
        bar_types_available="5m confirmed for 2024-06-01",
        trade_data_available="kline trade_count available",
        funding_rate_available="planned",
        mark_price_available="planned",
        index_price_available="planned",
        instrument_metadata_available="deterministic MVP perpetual mapping",
        vwm_compatible="true_trade_bar_with_funding_caveat",
        current_status="e4_multisymbol_vwm_smoke_passed",
        caveat="Funding, liquidation, margin, and mark/index price effects are not modeled in PnL",
        next_action="add funding-aware evaluation before performance claims",
    ),
    CryptoSupportRow(
        exchange="BINANCE",
        market_type="crypto_delivery_futures",
        symbol="BTCUSD delivery",
        instrument_id="BTCUSD_DELIVERY.BINANCE",
        raw_data_available="no local delivery futures data confirmed",
        bar_types_available="none confirmed",
        trade_data_available="none confirmed",
        funding_rate_available="not applicable or venue-specific",
        mark_price_available="planned",
        index_price_available="planned",
        instrument_metadata_available="planned",
        vwm_compatible="planned",
        current_status="adapter_code_available_data_missing",
        caveat="Binance Vision futures_cm adapter code exists, but scanned historical data has no futures_cm partitions",
        next_action="defer until perpetual path is validated",
    ),
    CryptoSupportRow(
        exchange="OKX",
        market_type="crypto_perpetual",
        symbol="BTC-USDT-SWAP",
        instrument_id="BTC-USDT-SWAP.OKX",
        raw_data_available="none confirmed",
        bar_types_available="none confirmed",
        trade_data_available="none confirmed",
        funding_rate_available="planned",
        mark_price_available="planned",
        index_price_available="planned",
        instrument_metadata_available="ccxt connector tests/mock coverage only",
        vwm_compatible="planned",
        current_status="connector_planned_no_data",
        caveat="OKX appears in connector tests/config examples, not as confirmed historical data",
        next_action="inventory or import OKX swap bars only after Binance perp path is validated",
    ),
    CryptoSupportRow(
        exchange="BYBIT",
        market_type="crypto_perpetual",
        symbol="BTCUSDT",
        instrument_id="BTCUSDT.BYBIT-PERP",
        raw_data_available="none confirmed",
        bar_types_available="none confirmed",
        trade_data_available="none confirmed",
        funding_rate_available="planned",
        mark_price_available="planned",
        index_price_available="planned",
        instrument_metadata_available="ccxt connector tests/mock coverage only",
        vwm_compatible="planned",
        current_status="connector_planned_no_data",
        caveat="Bybit appears in connector tests/config examples, not as confirmed historical data",
        next_action="keep planned until data source is approved",
    ),
)


def get_crypto_market(market_type: str, exchange: str = "BINANCE") -> CryptoMarketSpec:
    exchange_norm = exchange.upper()
    for spec in CRYPTO_MARKET_REGISTRY:
        if spec.market_type == market_type and spec.exchange == exchange_norm:
            return spec
    raise KeyError(f"unknown crypto market spec: {exchange}.{market_type}")


def infer_crypto_market_type(*, symbol: str, exchange: str = "BINANCE", venue_type: str) -> str:
    venue = venue_type.lower()
    if venue == "spot":
        return "crypto_spot"
    if venue in {"futures", "perpetual", "swap", "futures_um"}:
        return "crypto_perpetual"
    if venue in {"delivery", "delivery_future", "futures_cm"}:
        return "crypto_delivery_futures"
    raise KeyError(f"cannot infer crypto market type for {exchange}:{symbol} venue_type={venue_type!r}")


def render_crypto_support_matrix_markdown(rows: tuple[CryptoSupportRow, ...] = CRYPTO_SUPPORT_ROWS) -> str:
    header = (
        "| exchange | market_type | symbol | instrument_id | raw_data_available | "
        "bar_types_available | trade_data_available | funding_rate_available | "
        "mark_price_available | index_price_available | instrument_metadata_available | "
        "vwm_compatible | current_status | caveat | next_action |"
    )
    divider = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    return "\n".join([header, divider, *(row.to_markdown_row() for row in rows)])


def _escape_markdown(value: str) -> str:
    return value.replace("|", "\\|")
