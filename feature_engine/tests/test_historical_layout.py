"""historical_data 平级布局的路径构造测试（layout 为纯标准库）。"""
from __future__ import annotations

from feature_engine.storage.layout import (
    FEATURE_DATA_PARTITION_COLS,
    INSTRUMENTS_PARTITION_COLS,
    LEGACY_FEATURE_PARTITION_COLS,
    MARKET_DATA_PARTITION_COLS,
    feature_data_path,
    instruments_path,
    market_data_path,
    parse_partition_path,
)


def test_partition_column_orders():
    assert MARKET_DATA_PARTITION_COLS == (
        "asset_class", "exchange", "frequency", "trading_date", "instrument_id",
    )
    # feature_data 与 market_data 平级：把 instrument 提升为分区维度。
    assert FEATURE_DATA_PARTITION_COLS[0] == "feature_group"
    assert "instrument_id" in FEATURE_DATA_PARTITION_COLS
    assert INSTRUMENTS_PARTITION_COLS == ("exchange", "as_of_date")
    # 旧布局保留以便读取（标注 legacy）。
    assert "instrument_id" not in LEGACY_FEATURE_PARTITION_COLS


def test_market_data_path():
    p = market_data_path(
        "historical_data/market_data",
        asset_class="future", exchange="CFFEX", frequency="1m",
        trading_date="2026-05-26", instrument_id="IH2303.CFFEX",
    )
    assert p.as_posix() == (
        "historical_data/market_data/asset_class=future/exchange=CFFEX/"
        "frequency=1m/trading_date=2026-05-26/instrument_id=IH2303.CFFEX"
    )
    # 路径可被反解析回分区字典。
    parsed = parse_partition_path(p)
    assert parsed["asset_class"] == "future"
    assert parsed["instrument_id"] == "IH2303.CFFEX"


def test_feature_data_path_is_parallel_to_market():
    p = feature_data_path(
        "historical_data/feature_data",
        feature_group="technical", asset_class="future", exchange="CFFEX",
        frequency="1m", trading_date="2026-05-26", instrument_id="IH2303.CFFEX",
    )
    # market 与 feature 共享 asset_class/exchange/frequency/trading_date/instrument_id 维度。
    parsed = parse_partition_path(p)
    for k in ("asset_class", "exchange", "frequency", "trading_date", "instrument_id"):
        assert k in parsed
    assert parsed["feature_group"] == "technical"


def test_instruments_path():
    p = instruments_path("historical_data/instruments", exchange="CFFEX", as_of_date="2026-05-26")
    assert p.as_posix() == (
        "historical_data/instruments/exchange=CFFEX/as_of_date=2026-05-26"
    )
