from __future__ import annotations

from pathlib import Path

from research.adapter_registry import ADAPTER_REGISTRY
from research.adapter_registry import SUPPORT_MATRIX_ROWS
from research.adapter_registry import get_adapter
from research.adapter_registry import render_support_matrix_markdown
from research.adapter_registry import validate_registry
from research.data_type_adapters import CANONICAL_SCHEMAS
from research.data_type_adapters import MID_BAR_VOLUME_POLICIES
from research.data_type_adapters import render_canonical_schemas_markdown


def test_required_registry_entries_exist():
    raw_types = {spec.raw_type for spec in ADAPTER_REGISTRY}
    assert "ohlcv_bar" in raw_types
    assert "aggTrades" in raw_types
    assert "trade_tick" in raw_types
    assert "quote_tick" in raw_types
    assert "order_book_depth" in raw_types
    assert "futures_contract" in raw_types


def test_raw_types_map_to_expected_canonical_outputs():
    assert get_adapter("quote_tick").output_type == "canonical_mid_bar"
    assert get_adapter("order_book_depth").output_type == "canonical_mid_bar"
    assert get_adapter("futures_contract").output_type == "canonical_instrument_metadata"
    assert get_adapter("aggTrades").output_type == "canonical_trade_bar"
    assert get_adapter("trade_tick").output_type == "canonical_trade_bar"


def test_vwm_compatibility_classification():
    assert get_adapter("ohlcv_bar").vwm_compatibility == "true"
    assert get_adapter("aggTrades").vwm_compatibility == "true"
    assert get_adapter("trade_tick").vwm_compatibility == "true"
    assert get_adapter("quote_tick").vwm_compatibility == "smoke_only"
    assert get_adapter("order_book_depth").vwm_compatibility == "smoke_only"
    assert get_adapter("futures_contract").vwm_compatibility == "metadata_only"


def test_non_trade_and_metadata_adapters_require_caveats():
    for raw_type in ("quote_tick", "order_book_depth", "futures_contract"):
        spec = get_adapter(raw_type)
        assert spec.caveat
        assert spec.vwm_compatibility in {"smoke_only", "metadata_only"}
    assert "not trade OHLCV" in get_adapter("quote_tick").caveat
    assert "not trade OHLCV" in get_adapter("order_book_depth").caveat


def test_deterministic_mvp_metadata_source_is_marked_as_caveat():
    spec = get_adapter("futures_contract")
    assert spec.metadata_source == "deterministic_mvp"
    assert "deterministic_mvp" in spec.caveat
    assert "metadata only" in spec.caveat


def test_canonical_schemas_include_required_fields_and_policies():
    trade_fields = CANONICAL_SCHEMAS["canonical_trade_bar"].required_fields
    mid_fields = CANONICAL_SCHEMAS["canonical_mid_bar"].required_fields
    metadata_fields = CANONICAL_SCHEMAS["canonical_instrument_metadata"].required_fields
    assert "volume" in trade_fields
    assert "bar_source" in trade_fields
    assert "volume_policy" in mid_fields
    assert "is_trade_bar" in mid_fields
    assert "metadata_source" in metadata_fields
    assert "caveat" in metadata_fields
    assert "quote_update_count" in MID_BAR_VOLUME_POLICIES
    assert "depth_update_count" in MID_BAR_VOLUME_POLICIES


def test_registry_validates_against_known_canonical_schemas():
    validate_registry()


def test_support_matrix_rows_can_be_rendered_to_markdown():
    markdown = render_support_matrix_markdown()
    assert "| raw_data_type | current_source |" in markdown
    assert "OHLCV bar" in markdown
    assert "quote_tick" in markdown
    assert "order_book_depth" in markdown
    assert "futures_contract" in markdown
    assert "aggTrades / trade ticks" in markdown
    assert len(markdown.splitlines()) == len(SUPPORT_MATRIX_ROWS) + 2


def test_canonical_schemas_can_be_rendered_to_markdown():
    markdown = render_canonical_schemas_markdown()
    assert "canonical_trade_bar" in markdown
    assert "canonical_mid_bar" in markdown
    assert "canonical_instrument_metadata" in markdown


def test_support_matrix_doc_matches_registry_terms():
    doc = Path("docs/data_type_support_matrix.md").read_text(encoding="utf-8")
    for raw_type in ("ohlcv_bar", "aggTrades", "trade_tick", "quote_tick", "order_book_depth", "futures_contract"):
        assert raw_type in doc
    assert "smoke_only" in doc
    assert "metadata_only" in doc
    assert "not real traded OHLCV" in doc


def test_registry_modules_do_not_enter_execution_paths():
    forbidden_tokens = (
        "Backtest" + "Engine",
        "run_" + "strategy",
        "outputs/" + "backtests",
        "request" + "s.",
        "url" + "lib.",
        "sock" + "et.",
        "sub" + "process",
        "shell" + "=",
        "rm" + "tree",
        ".un" + "link(",
        ".rem" + "ove(",
        ".rm" + "dir(",
    )
    for path in (Path("research/data_type_adapters.py"), Path("research/adapter_registry.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in text
