from __future__ import annotations

import inspect

import pytest

import strategy_framework.backends.nautilus_native as native


def test_binance_spot_mappings_still_resolve():
    btc = native.resolve_instrument_mapping("BTCUSDT.BINANCE")
    eth = native.resolve_instrument_mapping("ETHUSDT.BINANCE")
    assert btc.kind == "test_kit_factory"
    assert btc.factory == "btcusdt_binance"
    assert btc.venue == "BINANCE"
    assert eth.kind == "test_kit_factory"
    assert eth.factory == "ethusdt_binance"


def test_binance_perpetual_mappings_resolve():
    btc = native.resolve_instrument_mapping("BTCUSDT-PERP.BINANCE")
    eth = native.resolve_instrument_mapping("ETHUSDT-PERP.BINANCE")
    assert btc.kind == "test_kit_factory"
    assert btc.factory == "btcusdt_perp_binance"
    assert btc.venue == "BINANCE"
    assert btc.symbol == "BTCUSDT-PERP"
    assert eth.kind == "test_kit_factory"
    assert eth.factory == "ethusdt_perp_binance"


def test_binance_multisymbol_perpetual_mvp_mappings_resolve():
    sol = native.resolve_instrument_mapping("SOLUSDT-PERP.BINANCE")
    bnb = native.resolve_instrument_mapping("BNBUSDT-PERP.BINANCE")
    assert sol.kind == "crypto_perpetual_mvp"
    assert sol.venue == "BINANCE"
    assert sol.symbol == "SOLUSDT-PERP"
    assert sol.quote_asset == "USDT"
    assert sol.settlement_asset == "USDT"
    assert sol.margin_asset == "USDT"
    assert sol.metadata_source == "deterministic_mvp"
    assert "not modeled" in sol.caveat
    assert bnb.kind == "crypto_perpetual_mvp"
    assert bnb.symbol == "BNBUSDT-PERP"
    assert bnb.underlying == "BNB"


def test_cffex_if2303_mapping_metadata():
    mapping = native.resolve_instrument_mapping("IF2303.CFFEX")
    assert mapping.kind == "cffex_futures_mvp"
    assert mapping.venue == "CFFEX"
    assert mapping.symbol == "IF2303"
    assert mapping.instrument_id == "IF2303.CFFEX"
    assert mapping.exchange == "CFFEX"
    assert mapping.asset_class == "INDEX"
    assert mapping.tick_size == "0.2"
    assert mapping.price_precision == 1
    assert mapping.lot_size == 1
    assert mapping.multiplier == 300
    assert mapping.currency == "CNY"
    assert mapping.underlying == "IF"
    assert mapping.metadata_source == "deterministic_mvp"


def test_cffex_symbol_without_venue_maps_when_exchange_given():
    mapping = native.resolve_instrument_mapping("IF2303", exchange="CFFEX")
    assert mapping.instrument_id == "IF2303.CFFEX"
    assert mapping.kind == "cffex_futures_mvp"


@pytest.mark.parametrize(
    ("symbol", "multiplier"),
    [
        ("IF2303.CFFEX", 300),
        ("IH2303.CFFEX", 300),
        ("IC2303.CFFEX", 200),
        ("IM2303.CFFEX", 200),
    ],
)
def test_cffex_series_multiplier_rules(symbol, multiplier):
    mapping = native.resolve_instrument_mapping(symbol)
    assert mapping.tick_size == "0.2"
    assert mapping.lot_size == 1
    assert mapping.multiplier == multiplier
    assert mapping.currency == "CNY"


def test_unsupported_symbol_raises_clear_error():
    with pytest.raises(native.NautilusUnavailableError, match="no instrument mapping"):
        native.resolve_instrument_mapping("AAPL.NASDAQ")


def test_source_scan_has_no_network_or_destructive_ops():
    src = inspect.getsource(native)
    forbidden = [
        "requests",
        "urllib",
        "websocket",
        "download",
        "live/" + "order/" + "account",
        "ScheduleWakeup",
        "shutil.rmtree",
        "shell" + "=True",
    ]
    for token in forbidden:
        assert token not in src
