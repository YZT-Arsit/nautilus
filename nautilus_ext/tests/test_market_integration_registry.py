from __future__ import annotations

from pathlib import Path

from research.adapter_registry import ADAPTER_REGISTRY
from research.market_integration_registry import MARKET_INTEGRATION_REGISTRY
from research.market_integration_registry import RAW_ADAPTER_MARKET_USAGE
from research.market_integration_registry import get_market_integration
from research.market_integration_registry import infer_market_type
from research.market_integration_registry import render_market_matrix_markdown
from research.market_integration_registry import render_raw_adapter_market_matrix_markdown
from research.market_integration_registry import validate_market_registry


def test_required_market_types_exist():
    market_types = {spec.market_type for spec in MARKET_INTEGRATION_REGISTRY}
    assert "crypto_spot" in market_types
    assert "equity_index_futures" in market_types
    assert "equities" in market_types
    assert "etfs" in market_types
    assert "indices" in market_types
    assert "options" in market_types


def test_options_exist_as_future_work():
    options = get_market_integration("options")
    assert options.current_status == "future_work"
    assert options.vwm_compatibility == "not_directly_compatible"


def test_instrument_examples_map_to_market_types():
    assert infer_market_type("BTCUSDT") == "crypto_spot"
    assert infer_market_type("BTCUSDT.BINANCE") == "crypto_spot"
    assert infer_market_type("IF2303.CFFEX") == "equity_index_futures"


def test_cffex_futures_require_instrument_metadata_and_smoke_only_quote_mid():
    spec = get_market_integration("equity_index_futures")
    assert spec.instrument_mapping_required is True
    assert "futures_contract" in spec.required_raw_data
    assert "canonical_instrument_metadata" in spec.canonical_data_output
    assert "smoke_only" in spec.vwm_compatibility
    assert "quote update count" in spec.volume_semantics
    assert "not real trade OHLCV" in spec.caveat


def test_equities_require_corporate_action_caveat():
    spec = get_market_integration("equities")
    assert "corporate_actions" in spec.required_raw_data
    assert "corporate_action_adjustment" in spec.required_metadata
    assert "corporate actions" in spec.caveat


def test_indices_are_analysis_only_and_non_tradable():
    spec = get_market_integration("indices")
    assert spec.vwm_compatibility == "analysis_only"
    assert "non_tradable_marker" in spec.required_metadata
    assert "non-tradable" in spec.caveat


def test_market_matrix_renders_to_markdown():
    markdown = render_market_matrix_markdown()
    assert "| market_type | asset_class |" in markdown
    assert "crypto_spot" in markdown
    assert "equity_index_futures" in markdown
    assert "options" in markdown
    assert len(markdown.splitlines()) == len(MARKET_INTEGRATION_REGISTRY) + 2


def test_raw_adapter_matrix_and_market_matrix_are_distinct():
    market_markdown = render_market_matrix_markdown()
    adapter_markdown = render_raw_adapter_market_matrix_markdown()
    assert "| raw_data_type | adapter |" in adapter_markdown
    assert "| market_type | asset_class |" in market_markdown
    assert "used_by_market_types" in adapter_markdown
    assert "session_model" not in adapter_markdown
    assert "crypto_spot" in adapter_markdown
    assert "ohlcv_bar" not in market_markdown.splitlines()[0]


def test_registry_validates_against_raw_adapter_usage():
    validate_market_registry()
    raw_types = {adapter.raw_type for adapter in ADAPTER_REGISTRY}
    assert set(RAW_ADAPTER_MARKET_USAGE).issubset(raw_types)


def test_docs_include_layer_distinction_and_market_terms():
    doc = Path("docs/market_type_support_matrix.md").read_text(encoding="utf-8")
    assert "Market type / asset class determines metadata" in doc
    assert "Raw data type determines" in doc
    assert "equity_index_futures" in doc
    assert "not_directly_compatible" in doc


def test_market_registry_has_no_execution_or_network_imports():
    forbidden_tokens = (
        "nautilus_" + "trader",
        "Backtest" + "Engine",
        "request" + "s",
        "url" + "lib",
        "web" + "socket",
        "sub" + "process",
        "shell" + "=",
        "rm" + "tree",
        ".un" + "link(",
        ".rem" + "ove(",
        ".rm" + "dir(",
    )
    text = Path("research/market_integration_registry.py").read_text(encoding="utf-8")
    for token in forbidden_tokens:
        assert token not in text
