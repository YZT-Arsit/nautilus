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
    assert "funding_rate" in spec.optional_metadata
    assert "mark_price" in spec.optional_metadata
    assert "index_price" in spec.optional_metadata
    assert "Funding" in spec.caveat
    assert spec.vwm_compatibility == "true_trade_bar_missing_metadata"


def test_vwm_true_only_for_confirmed_trade_bars():
    rows = {row.instrument_id: row for row in CRYPTO_SUPPORT_ROWS}
    assert rows["BTCUSDT.BINANCE"].vwm_compatible == "true_trade_bar"
    assert rows["BTCUSDT.BINANCE-PERP"].vwm_compatible == "planned"
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
    assert "No confirmed local/remote historical partitions for Binance `futures_um`" in doc
    assert "adapter_code_available_data_missing" in doc
    assert "true_trade_bar" in doc


def test_crypto_registry_has_no_nautilus_imports():
    text = Path("research/crypto_market_registry.py").read_text(encoding="utf-8")
    forbidden = ("nautilus_" + "trader", "request" + "s", "url" + "lib", "web" + "socket")
    for token in forbidden:
        assert token not in text
