"""Data source adapters for importing historical market data.

This package contains adapters to ingest data from various exchanges and
normalize to StandardBar schema for integration with feature engine pipelines.
"""

from feature_engine.data_sources.binance_vision import (
    BinanceVisionImporter,
    build_binance_vision_aggtrades_url,
    build_binance_vision_kline_url,
    normalize_binance_aggtrades,
    normalize_binance_kline,
    read_binance_aggtrades_zip,
    read_binance_kline_zip,
    build_binance_vision_funding_url,
    read_binance_funding_zip,
    normalize_binance_funding,
    read_binance_funding_api,
    Frequency,
    Market,
)

__all__ = [
    "BinanceVisionImporter",
    "build_binance_vision_kline_url",
    "read_binance_kline_zip",
    "normalize_binance_kline",
    "build_binance_vision_aggtrades_url",
    "read_binance_aggtrades_zip",
    "normalize_binance_aggtrades",
    "build_binance_vision_funding_url",
    "read_binance_funding_zip",
    "normalize_binance_funding",
    "read_binance_funding_api",
    "Market",
    "Frequency",
]
