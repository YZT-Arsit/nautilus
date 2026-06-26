from __future__ import annotations

from pathlib import Path

from research.crypto_market_registry import CRYPTO_MARKET_REGISTRY
from research.crypto_market_registry import CRYPTO_SUPPORT_ROWS
from research.crypto_market_registry import get_crypto_market
from research.crypto_market_registry import infer_crypto_market_type
from research.crypto_market_registry import render_crypto_support_matrix_markdown


def test_required_crypto_market_entries_exist():
    market_types = {spec.market_type for spec in CRYPTO_MARKET_REGISTRY}
    assert "crypto_spot" in market_types
    assert "crypto_perpetual" in market_types
    assert "crypto_delivery_futures" in market_types


def test_binance_spot_and_perpetual_mapping():
    assert infer_crypto_market_type(symbol="BTCUSDT", exchange="BINANCE", venue_type="spot") == "crypto_spot"
    assert infer_crypto_market_type(symbol="ETHUSDT", exchange="BINANCE", venue_type="spot") == "crypto_spot"
    assert (
        infer_crypto_market_type(symbol="BTCUSDT", exchange="BINANCE", venue_type="futures")
        == "crypto_perpetual"
    )
    assert (
        infer_crypto_market_type(symbol="ETHUSDT", exchange="BINANCE", venue_type="perpetual")
        == "crypto_perpetual"
    )
    assert (
        infer_crypto_market_type(symbol="BTCUSD", exchange="BINANCE", venue_type="futures_cm")
        == "crypto_delivery_futures"
    )


def test_required_metadata_for_crypto_markets():
    for market_type in ("crypto_spot", "crypto_perpetual", "crypto_delivery_futures"):
        spec = get_crypto_market(market_type)
        assert "tick_size" in spec.required_metadata
        assert "lot_size" in spec.required_metadata
        assert "fee_model" in spec.required_metadata


def test_perpetual_metadata_and_funding_caveat():
    spec = get_crypto_market("crypto_perpetual")
    assert "exchange_info" in spec.optional_metadata
    assert "funding_rate" in spec.optional_metadata
    assert "mark_price" in spec.optional_metadata
    assert "index_price" in spec.optional_metadata
    assert "canonical_funding_rate" in spec.canonical_data
    assert "canonical_mark_index_price" in spec.canonical_data
    assert "Funding" in spec.caveat
    assert spec.vwm_compatibility == "true_trade_bar_with_funding_caveat"
    assert spec.status == "funding_mark_index_smoke_validated_exchange_info_network_blocked"


def test_vwm_true_only_for_confirmed_trade_bars():
    rows = {row.instrument_id: row for row in CRYPTO_SUPPORT_ROWS}
    assert rows["BTCUSDT.BINANCE"].vwm_compatible == "true_trade_bar"
    assert rows["BTCUSDT-PERP.BINANCE"].vwm_compatible == "true_trade_bar_with_funding_caveat"
    assert rows["BTCUSDT-PERP.BINANCE"].funding_rate_available == "smoke_validated"
    assert rows["BTCUSDT-PERP.BINANCE"].mark_price_available == "smoke_validated"
    assert rows["BTCUSDT-PERP.BINANCE"].index_price_available == "smoke_validated"
    assert rows["ETHUSDT-PERP.BINANCE"].current_status == "e4_multisymbol_vwm_smoke_passed"
    assert rows["SOLUSDT-PERP.BINANCE"].current_status == "e4_multisymbol_vwm_smoke_passed"
    assert rows["BNBUSDT-PERP.BINANCE"].vwm_compatible == "true_trade_bar_with_funding_caveat"
    assert rows["BTC-USDT-SWAP.OKX"].current_status == "connector_planned_no_data"
    assert rows["BTCUSDT.BYBIT-PERP"].current_status == "connector_planned_no_data"


def test_render_crypto_support_matrix_markdown():
    markdown = render_crypto_support_matrix_markdown()
    assert "| exchange | market_type | symbol |" in markdown
    assert "BTCUSDT.BINANCE" in markdown
    assert "crypto_perpetual" in markdown
    assert "funding_rate_available" in markdown
    assert len(markdown.splitlines()) == len(CRYPTO_SUPPORT_ROWS) + 2


def test_crypto_support_doc_reports_confirmed_vs_planned():
    doc = Path("docs/crypto_futures_support_matrix.md").read_text(encoding="utf-8")
    assert "Confirmed Data Sources" in doc
    assert "E2 added confirmed Binance `futures_um` 5m partitions" in doc
    assert "E3 added Binance public USD-M metadata smoke" in doc
    assert "e4_multisymbol_vwm_smoke_passed" in doc
    assert "true_trade_bar" in doc


def test_crypto_registry_has_no_nautilus_imports():
    text = Path("research/crypto_market_registry.py").read_text(encoding="utf-8")
    forbidden = ("nautilus_" + "trader", "request" + "s", "url" + "lib", "web" + "socket")
    for token in forbidden:
        assert token not in text
