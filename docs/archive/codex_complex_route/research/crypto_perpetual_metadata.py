"""Canonical metadata helpers for Binance USD-M perpetual smoke data."""
from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode


EXCHANGE = "BINANCE"
VENUE_TYPE = "futures_um"
MARKET_TYPE = "crypto_perpetual"
METADATA_SOURCE = "binance_fapi_public"
CAVEAT = "Metadata is collected but funding, mark/index price, and margin effects are not applied to PnL."
FUNDING_INTERVAL_HOURS = 8
BASE_URL = "https://fapi.binance.com/fapi/v1"
VISION_BASE_URL = "https://data.binance.vision/data/futures/um"


@dataclass(frozen=True)
class PerpetualInstrumentMetadata:
    exchange: str
    venue_type: str
    symbol: str
    instrument_id: str
    market_type: str
    contract_type: str
    base_asset: str
    quote_asset: str
    settlement_asset: str
    margin_asset: str
    tick_size: str
    lot_size: str
    price_precision: int
    quantity_precision: int
    min_qty: str
    min_notional: str
    status: str
    metadata_source: str
    fetched_at: str
    caveat: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FundingRateRecord:
    ts: str
    exchange: str
    venue_type: str
    symbol: str
    instrument_id: str
    funding_rate: float
    funding_time: str
    funding_interval_hours: int
    source: str
    ingested_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MarkIndexPriceRecord:
    ts: str
    exchange: str
    venue_type: str
    symbol: str
    instrument_id: str
    mark_price: float
    index_price: float
    estimated_settle_price: float | None
    last_funding_rate: float | None
    next_funding_time: str | None
    source: str
    ingested_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def instrument_id(symbol: str) -> str:
    return f"{symbol.upper()}-PERP.BINANCE"


def utc_day_bounds_ms(day: str) -> tuple[int, int]:
    parsed = date.fromisoformat(day)
    start = datetime.combine(parsed, time.min, tzinfo=timezone.utc)
    end = datetime.combine(parsed, time.max, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def iso_from_ms(value: int | str | None) -> str | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()


def build_exchange_info_url() -> str:
    return f"{BASE_URL}/exchangeInfo"


def build_funding_rate_url(symbol: str, day: str) -> str:
    start_ms, end_ms = utc_day_bounds_ms(day)
    query = urlencode({"symbol": symbol.upper(), "startTime": start_ms, "endTime": end_ms, "limit": 100})
    return f"{BASE_URL}/fundingRate?{query}"


def build_mark_price_url(symbol: str, day: str, interval: str = "5m") -> str:
    start_ms, end_ms = utc_day_bounds_ms(day)
    query = urlencode(
        {"symbol": symbol.upper(), "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": 1500}
    )
    return f"{BASE_URL}/markPriceKlines?{query}"


def build_index_price_url(symbol: str, day: str, interval: str = "5m") -> str:
    start_ms, end_ms = utc_day_bounds_ms(day)
    query = urlencode(
        {"pair": symbol.upper(), "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": 1500}
    )
    return f"{BASE_URL}/indexPriceKlines?{query}"


def build_funding_rate_archive_url(symbol: str, day: str) -> str:
    month = day[:7]
    return f"{VISION_BASE_URL}/monthly/fundingRate/{symbol.upper()}/{symbol.upper()}-fundingRate-{month}.zip"


def build_mark_price_archive_url(symbol: str, day: str, interval: str = "5m") -> str:
    symbol_norm = symbol.upper()
    return f"{VISION_BASE_URL}/daily/markPriceKlines/{symbol_norm}/{interval}/{symbol_norm}-{interval}-{day}.zip"


def build_index_price_archive_url(symbol: str, day: str, interval: str = "5m") -> str:
    symbol_norm = symbol.upper()
    return f"{VISION_BASE_URL}/daily/indexPriceKlines/{symbol_norm}/{interval}/{symbol_norm}-{interval}-{day}.zip"


def normalize_exchange_info(payload: dict[str, Any], *, symbol: str, fetched_at: str) -> PerpetualInstrumentMetadata:
    item = _find_symbol(payload, symbol)
    filters = {row.get("filterType"): row for row in item.get("filters", [])}
    price_filter = filters.get("PRICE_FILTER", {})
    lot_filter = filters.get("LOT_SIZE", {})
    notional_filter = filters.get("MIN_NOTIONAL", {}) or filters.get("NOTIONAL", {})
    return PerpetualInstrumentMetadata(
        exchange=EXCHANGE,
        venue_type=VENUE_TYPE,
        symbol=symbol.upper(),
        instrument_id=instrument_id(symbol),
        market_type=MARKET_TYPE,
        contract_type=str(item.get("contractType", "PERPETUAL")),
        base_asset=str(item.get("baseAsset", "")),
        quote_asset=str(item.get("quoteAsset", "")),
        settlement_asset=str(item.get("marginAsset", item.get("quoteAsset", ""))),
        margin_asset=str(item.get("marginAsset", item.get("quoteAsset", ""))),
        tick_size=_decimal_string(price_filter.get("tickSize", "0")),
        lot_size=_decimal_string(lot_filter.get("stepSize", "0")),
        price_precision=int(item.get("pricePrecision", 0)),
        quantity_precision=int(item.get("quantityPrecision", 0)),
        min_qty=_decimal_string(lot_filter.get("minQty", "0")),
        min_notional=_decimal_string(notional_filter.get("notional", notional_filter.get("minNotional", "0"))),
        status=str(item.get("status", "")),
        metadata_source=METADATA_SOURCE,
        fetched_at=fetched_at,
        caveat=CAVEAT,
    )


def normalize_funding_rates(payload: list[dict[str, Any]], *, symbol: str, ingested_at: str) -> list[FundingRateRecord]:
    rows: list[FundingRateRecord] = []
    for item in payload:
        funding_time = iso_from_ms(item.get("fundingTime"))
        if funding_time is None:
            continue
        rows.append(
            FundingRateRecord(
                ts=funding_time,
                exchange=EXCHANGE,
                venue_type=VENUE_TYPE,
                symbol=symbol.upper(),
                instrument_id=instrument_id(symbol),
                funding_rate=float(item.get("fundingRate", 0.0)),
                funding_time=funding_time,
                funding_interval_hours=FUNDING_INTERVAL_HOURS,
                source=METADATA_SOURCE + "_fundingRate",
                ingested_at=ingested_at,
            )
        )
    return rows


def normalize_mark_index_prices(
    mark_rows: list[list[Any]],
    index_rows: list[list[Any]],
    *,
    symbol: str,
    ingested_at: str,
) -> list[MarkIndexPriceRecord]:
    index_by_ts = {int(row[0]): row for row in index_rows if row}
    out: list[MarkIndexPriceRecord] = []
    for mark in mark_rows:
        if not mark:
            continue
        ts_ms = int(mark[0])
        index = index_by_ts.get(ts_ms)
        if index is None:
            continue
        ts = iso_from_ms(ts_ms)
        if ts is None:
            continue
        out.append(
            MarkIndexPriceRecord(
                ts=ts,
                exchange=EXCHANGE,
                venue_type=VENUE_TYPE,
                symbol=symbol.upper(),
                instrument_id=instrument_id(symbol),
                mark_price=float(mark[4]),
                index_price=float(index[4]),
                estimated_settle_price=None,
                last_funding_rate=None,
                next_funding_time=None,
                source=METADATA_SOURCE + "_markPriceKlines_indexPriceKlines",
                ingested_at=ingested_at,
            )
        )
    return out


def required_instrument_fields() -> tuple[str, ...]:
    return tuple(PerpetualInstrumentMetadata.__dataclass_fields__)


def required_funding_fields() -> tuple[str, ...]:
    return tuple(FundingRateRecord.__dataclass_fields__)


def required_mark_index_fields() -> tuple[str, ...]:
    return tuple(MarkIndexPriceRecord.__dataclass_fields__)


def validate_endpoint(url: str) -> None:
    allowed_prefixes = (BASE_URL + "/", VISION_BASE_URL + "/")
    if not url.startswith(allowed_prefixes):
        raise ValueError(f"unsupported metadata endpoint: {url}")
    lowered = url.lower()
    forbidden = (
        "api" + "key",
        "sign" + "ature",
        "listen" + "key",
        "user" + "data",
        ("margin" + "Type").lower(),
    )
    for token in forbidden:
        if token in lowered:
            raise ValueError(f"disallowed endpoint token: {token}")


def _find_symbol(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    symbol_norm = symbol.upper()
    for item in payload.get("symbols", []):
        if item.get("symbol") == symbol_norm:
            return item
    raise KeyError(f"symbol not found in exchangeInfo payload: {symbol_norm}")


def _decimal_string(value: Any) -> str:
    return format(Decimal(str(value)).normalize(), "f")
