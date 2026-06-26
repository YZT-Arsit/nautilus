from __future__ import annotations

from pathlib import Path

from research.crypto_perpetual_metadata import CAVEAT
from research.crypto_perpetual_metadata import build_funding_rate_url
from research.crypto_perpetual_metadata import build_index_price_url
from research.crypto_perpetual_metadata import build_mark_price_url
from research.crypto_perpetual_metadata import instrument_id
from research.crypto_perpetual_metadata import normalize_exchange_info
from research.crypto_perpetual_metadata import normalize_funding_rates
from research.crypto_perpetual_metadata import normalize_mark_index_prices
from research.crypto_perpetual_metadata import required_funding_fields
from research.crypto_perpetual_metadata import required_instrument_fields
from research.crypto_perpetual_metadata import required_mark_index_fields


def _exchange_info_payload(symbol: str = "BTCUSDT") -> dict[str, object]:
    return {
        "symbols": [
            {
                "symbol": symbol,
                "contractType": "PERPETUAL",
                "baseAsset": symbol.replace("USDT", ""),
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "pricePrecision": 2,
                "quantityPrecision": 3,
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "100"},
                ],
            }
        ]
    }


def test_exchange_info_normalization_required_fields():
    record = normalize_exchange_info(_exchange_info_payload(), symbol="BTCUSDT", fetched_at="2026-06-25T00:00:00+00:00")
    data = record.to_dict()
    for field in required_instrument_fields():
        assert field in data
    assert data["exchange"] == "BINANCE"
    assert data["venue_type"] == "futures_um"
    assert data["instrument_id"] == "BTCUSDT-PERP.BINANCE"
    assert data["contract_type"] == "PERPETUAL"
    assert data["base_asset"] == "BTC"
    assert data["quote_asset"] == "USDT"
    assert data["settlement_asset"] == "USDT"
    assert data["margin_asset"] == "USDT"
    assert data["tick_size"] == "0.1"
    assert data["lot_size"] == "0.001"
    assert data["min_notional"] == "100"
    assert data["status"] == "TRADING"
    assert "not applied to PnL" in data["caveat"]


def test_funding_rate_normalization_infers_interval_and_symbols():
    payload = [{"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1717200000000}]
    rows = normalize_funding_rates(payload, symbol="BTCUSDT", ingested_at="2026-06-25T00:00:00+00:00")
    data = rows[0].to_dict()
    for field in required_funding_fields():
        assert field in data
    assert data["symbol"] == "BTCUSDT"
    assert data["instrument_id"] == "BTCUSDT-PERP.BINANCE"
    assert data["funding_rate"] == 0.0001
    assert data["funding_interval_hours"] == 8
    assert data["funding_time"].startswith("2024-06-01T")


def test_mark_index_price_normalization_merges_by_timestamp():
    mark = [[1717200000000, "67000", "67100", "66900", "67050", "0", 1717200299999, "0", 0, "0", "0", "0"]]
    index = [[1717200000000, "66990", "67090", "66890", "67040", "0", 1717200299999, "0", 0, "0", "0", "0"]]
    rows = normalize_mark_index_prices(mark, index, symbol="ETHUSDT", ingested_at="2026-06-25T00:00:00+00:00")
    data = rows[0].to_dict()
    for field in required_mark_index_fields():
        assert field in data
    assert data["instrument_id"] == "ETHUSDT-PERP.BINANCE"
    assert data["mark_price"] == 67050.0
    assert data["index_price"] == 67040.0
    assert data["estimated_settle_price"] is None
    assert data["last_funding_rate"] is None
    assert data["next_funding_time"] is None


def test_instrument_id_convention_for_btc_and_eth():
    assert instrument_id("BTCUSDT") == "BTCUSDT-PERP.BINANCE"
    assert instrument_id("ethusdt") == "ETHUSDT-PERP.BINANCE"


def test_public_endpoint_construction_has_expected_paths():
    funding = build_funding_rate_url("BTCUSDT", "2024-06-01")
    mark = build_mark_price_url("BTCUSDT", "2024-06-01")
    index = build_index_price_url("BTCUSDT", "2024-06-01")
    assert funding.startswith("https://fapi.binance.com/fapi/v1/fundingRate?")
    assert "symbol=BTCUSDT" in funding
    assert "markPriceKlines" in mark
    assert "indexPriceKlines" in index
    assert "startTime=1717200000000" in funding


def test_metadata_module_has_no_disallowed_endpoint_terms():
    text = Path("research/crypto_perpetual_metadata.py").read_text(encoding="utf-8")
    forbidden = (
        "api" + "Key",
        "listen" + "Key",
        "user" + "DataStream",
        "priv" + "ate",
        "acc" + "ount",
        "bal" + "ance",
        "pos" + "ition",
        "lev" + "erage",
        "cancel",
        "create_" + "order",
        "shell" + "=True",
    )
    for token in forbidden:
        assert token not in text


def test_perpetual_metadata_caveat_exists_for_backtest_gap():
    assert "not applied to PnL" in CAVEAT
