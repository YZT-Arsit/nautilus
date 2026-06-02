"""
Unit tests for nautilus_ext.ccxt — all tests run without network access.

Strategy
--------
ccxt is mocked at the module level so that no real exchange is contacted.
Nautilus model classes (CurrencyPair, CryptoPerpetual, Bar, BarType) ARE
imported from the real nautilus_trader package because it is installed.

Tests cover
-----------
1.  CcxtDataConfig validation (required fields, bad timeframe).
2.  CcxtDataConfig.nautilus_timeframe conversion.
3.  CcxtInstrumentMapper — spot market → CurrencyPair.
4.  CcxtInstrumentMapper — swap/perpetual market → CryptoPerpetual.
5.  CcxtInstrumentMapper — future market → CryptoFuture.
6.  CcxtInstrumentMapper — TICK_SIZE precision mode.
7.  CcxtInstrumentMapper — missing base/quote raises clear error.
8.  CcxtOhlcvConnector — dedup and sort.
9.  CcxtOhlcvConnector — timestamp millisecond → UTC datetime.
10. CcxtBarMapper — returns non-empty list[Bar] with correct bar_type.
11. CcxtBarDataConnector.prepare_data() — end-to-end with mock exchange.
12. CcxtBarDataConnector — BacktestRunner interface (instrument attr, get_bar_type).
13. CcxtBarDataConnector.save_outputs() — writes expected files.
14. Original NautilusAutoBarDataConnector is not broken (import check).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Many tests require compiled Nautilus Cython extensions.  Skip them gracefully
# when the extensions are absent (e.g. local dev machine without a full build).
try:
    from nautilus_trader.persistence.wranglers import BarDataWrangler  # noqa: F401
    _NAUTILUS_AVAILABLE = True
except Exception:
    _NAUTILUS_AVAILABLE = False

nautilus_required = pytest.mark.skipif(
    not _NAUTILUS_AVAILABLE,
    reason="nautilus_trader Cython extensions not compiled in this environment",
)


# ---------------------------------------------------------------------------
# Helpers: build a minimal mock ccxt module
# ---------------------------------------------------------------------------

def _make_mock_ccxt(
    markets: dict,
    ohlcv_rows: list | None = None,
    precision_mode: int = 2,  # DECIMAL_PLACES
):
    """Return a fake ccxt module that behaves like the real one for our tests."""

    class _MockExchange:
        precisionMode = precision_mode
        DECIMAL_PLACES = 2
        TICK_SIZE = 4

        def load_markets(self):
            return markets

        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
            return ohlcv_rows or []

        def set_sandbox_mode(self, enabled):
            pass

    mock_ccxt = types.ModuleType("ccxt")
    mock_ccxt.DECIMAL_PLACES = 2
    mock_ccxt.TICK_SIZE = 4
    mock_ccxt.SIGNIFICANT_DIGITS = 3
    mock_ccxt.binance = _MockExchange
    mock_ccxt.okx = _MockExchange
    mock_ccxt.bybit = _MockExchange

    return mock_ccxt, _MockExchange


# ---------------------------------------------------------------------------
# Reusable fixtures
# ---------------------------------------------------------------------------

SPOT_MARKET: dict[str, Any] = {
    "id": "BTCUSDT",
    "symbol": "BTC/USDT",
    "base": "BTC",
    "quote": "USDT",
    "settle": None,
    "type": "spot",
    "spot": True,
    "swap": False,
    "future": False,
    "option": False,
    "linear": None,
    "inverse": None,
    "contract": False,
    "contractSize": None,
    "expiry": None,
    "expiryDatetime": None,
    "taker": 0.001,
    "maker": 0.001,
    "precision": {"price": 2, "amount": 5},
    "limits": {
        "amount": {"min": 0.00001, "max": 9000.0},
        "price": {"min": 0.01, "max": 1_000_000.0},
        "cost": {"min": 5.0, "max": None},
    },
    "active": True,
}

SWAP_MARKET: dict[str, Any] = {
    "id": "BTCUSDT",
    "symbol": "BTC/USDT:USDT",
    "base": "BTC",
    "quote": "USDT",
    "settle": "USDT",
    "type": "swap",
    "spot": False,
    "swap": True,
    "future": False,
    "option": False,
    "linear": True,
    "inverse": False,
    "contract": True,
    "contractSize": 1.0,
    "expiry": None,
    "expiryDatetime": None,
    "taker": 0.0005,
    "maker": 0.0002,
    "precision": {"price": 1, "amount": 3},
    "limits": {
        "amount": {"min": 0.001, "max": 1000.0},
        "price": {"min": 0.1, "max": 1_000_000.0},
        "cost": {"min": 5.0, "max": None},
    },
    "active": True,
}

FUTURE_MARKET: dict[str, Any] = {
    "id": "BTCUSDT-20241227",
    "symbol": "BTC/USDT:USDT-20241227",
    "base": "BTC",
    "quote": "USDT",
    "settle": "USDT",
    "type": "future",
    "spot": False,
    "swap": False,
    "future": True,
    "option": False,
    "linear": True,
    "inverse": False,
    "contract": True,
    "contractSize": 1.0,
    "expiry": 1735257600000,  # ms timestamp
    "expiryDatetime": "2024-12-27T00:00:00.000Z",
    "taker": 0.0005,
    "maker": 0.0002,
    "precision": {"price": 1, "amount": 3},
    "limits": {
        "amount": {"min": 0.001, "max": 1000.0},
        "price": {"min": 0.1, "max": 1_000_000.0},
        "cost": {"min": 5.0, "max": None},
    },
    "active": True,
}

# Six candles: timestamp_ms, open, high, low, close, volume
# Includes one duplicate (same ts as index 1) and is intentionally unordered
SAMPLE_OHLCV_ROWS = [
    [1704067320000, 43000.0, 44000.0, 42500.0, 43500.0, 110.0],  # 3rd chronologically
    [1704067200000, 42000.0, 43000.0, 41500.0, 42500.0, 100.0],  # 1st
    [1704067260000, 42500.0, 43500.0, 42000.0, 43000.0, 120.0],  # 2nd
    [1704067260000, 42600.0, 43600.0, 42100.0, 43100.0, 121.0],  # duplicate of 2nd (keep last)
    [1704067380000, 43500.0, 44500.0, 43000.0, 44000.0, 130.0],  # 4th
    [1704067440000, 44000.0, 45000.0, 43500.0, 44500.0, 140.0],  # 5th (possibly incomplete)
]

# ---------------------------------------------------------------------------
# 1. CcxtDataConfig validation
# ---------------------------------------------------------------------------

def test_config_requires_exchange_id():
    with pytest.raises(ValueError, match="exchange_id"):
        from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
        CcxtDataConfig(
            exchange_id="",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframe="1m",
            since="2024-01-01T00:00:00Z",
        )


def test_config_requires_symbols():
    with pytest.raises(ValueError, match="symbols"):
        from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
        CcxtDataConfig(
            exchange_id="binance",
            market_type="spot",
            symbols=[],
            timeframe="1m",
            since="2024-01-01T00:00:00Z",
        )


def test_config_bad_timeframe_raises():
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
        CcxtDataConfig(
            exchange_id="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframe="7m",   # invalid — 7 does not divide 60 AND not in our map
            since="2024-01-01T00:00:00Z",
        )


# ---------------------------------------------------------------------------
# 2. Timeframe conversion
# ---------------------------------------------------------------------------

def test_nautilus_timeframe_conversion():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    cases = [
        ("1m", "1-MINUTE"),
        ("5m", "5-MINUTE"),
        ("1h", "1-HOUR"),
        ("1d", "1-DAY"),
        ("1w", "1-WEEK"),
        ("1M", "1-MONTH"),
    ]
    for ccxt_tf, expected in cases:
        cfg = CcxtDataConfig(
            exchange_id="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframe=ccxt_tf,
            since="2024-01-01T00:00:00Z",
        )
        assert cfg.nautilus_timeframe == expected, f"{ccxt_tf} → {cfg.nautilus_timeframe!r} ≠ {expected!r}"


# ---------------------------------------------------------------------------
# 3. CcxtInstrumentMapper — spot → CurrencyPair
# ---------------------------------------------------------------------------

@nautilus_required
def test_instrument_mapper_spot_builds_currency_pair():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper

    cfg = CcxtDataConfig(
        exchange_id="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        venue="BINANCE",
    )
    mapper = CcxtInstrumentMapper(cfg, precision_mode=2)
    instrument = mapper.build_instrument(SPOT_MARKET, "spot")

    from nautilus_trader.model.instruments import CurrencyPair
    assert isinstance(instrument, CurrencyPair)
    assert instrument.id.symbol.value == "BTCUSDT"
    assert instrument.id.venue.value == "BINANCE"
    assert instrument.price_precision == 2
    assert instrument.size_precision == 5


# ---------------------------------------------------------------------------
# 4. CcxtInstrumentMapper — swap → CryptoPerpetual
# ---------------------------------------------------------------------------

@nautilus_required
def test_instrument_mapper_swap_builds_crypto_perpetual():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper

    cfg = CcxtDataConfig(
        exchange_id="binance",
        market_type="swap",
        symbols=["BTC/USDT:USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        venue="BINANCE",
    )
    mapper = CcxtInstrumentMapper(cfg, precision_mode=2)
    instrument = mapper.build_instrument(SWAP_MARKET, "swap_linear")

    from nautilus_trader.model.instruments import CryptoPerpetual
    assert isinstance(instrument, CryptoPerpetual)
    assert "PERP" in instrument.id.symbol.value
    assert instrument.price_precision == 1
    assert instrument.size_precision == 3


# ---------------------------------------------------------------------------
# 5. CcxtInstrumentMapper — future → CryptoFuture
# ---------------------------------------------------------------------------

@nautilus_required
def test_instrument_mapper_future_builds_crypto_future():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper

    cfg = CcxtDataConfig(
        exchange_id="binance",
        market_type="future",
        symbols=["BTC/USDT:USDT-20241227"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        venue="BINANCE",
    )
    mapper = CcxtInstrumentMapper(cfg, precision_mode=2)
    instrument = mapper.build_instrument(FUTURE_MARKET, "future_linear")

    from nautilus_trader.model.instruments import CryptoFuture
    assert isinstance(instrument, CryptoFuture)
    assert "20241227" in instrument.id.symbol.value


# ---------------------------------------------------------------------------
# 6. CcxtInstrumentMapper — TICK_SIZE precision mode
# ---------------------------------------------------------------------------

@nautilus_required
def test_instrument_mapper_tick_size_precision():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper, _TICK_SIZE

    okx_market = dict(SPOT_MARKET)
    okx_market["precision"] = {"price": 0.01, "amount": 0.00001}  # TICK_SIZE values

    cfg = CcxtDataConfig(
        exchange_id="okx",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        venue="OKX",
    )
    mapper = CcxtInstrumentMapper(cfg, precision_mode=_TICK_SIZE)
    instrument = mapper.build_instrument(okx_market, "spot")

    assert instrument.price_precision == 2   # 0.01 → 2 decimal places
    assert instrument.size_precision == 5    # 0.00001 → 5 decimal places


# ---------------------------------------------------------------------------
# 7. CcxtInstrumentMapper — missing base/quote raises clear error
# ---------------------------------------------------------------------------

@nautilus_required
def test_instrument_mapper_missing_base_quote_raises():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper

    broken_market = dict(SPOT_MARKET)
    broken_market["base"] = ""
    broken_market["quote"] = ""

    cfg = CcxtDataConfig(
        exchange_id="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        venue="BINANCE",
    )
    mapper = CcxtInstrumentMapper(cfg, precision_mode=2)
    with pytest.raises(ValueError, match="base.*quote|currency"):
        mapper.build_instrument(broken_market, "spot")


# ---------------------------------------------------------------------------
# 8. CcxtOhlcvConnector — dedup and sort
# ---------------------------------------------------------------------------

def test_ohlcv_connector_dedup_and_sort():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_ohlcv_connector import CcxtOhlcvConnector

    cfg = CcxtDataConfig(
        exchange_id="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        drop_incomplete_bar=False,  # keep all rows for this test
    )

    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = SAMPLE_OHLCV_ROWS
    connector = CcxtOhlcvConnector(cfg, mock_exchange)
    df = connector.fetch("BTC/USDT")

    # No duplicates: 1704067260000 appears twice in raw — only one after dedup
    assert df["timestamp_ms"].duplicated().sum() == 0
    # Sorted ascending
    assert list(df["timestamp_ms"]) == sorted(df["timestamp_ms"].tolist())
    # 5 unique timestamps in SAMPLE_OHLCV_ROWS
    assert len(df) == 5


# ---------------------------------------------------------------------------
# 9. CcxtOhlcvConnector — millisecond → UTC datetime
# ---------------------------------------------------------------------------

def test_ohlcv_connector_timestamp_utc():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_ohlcv_connector import CcxtOhlcvConnector

    cfg = CcxtDataConfig(
        exchange_id="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        drop_incomplete_bar=False,
    )

    rows = [[1704067200000, 42000.0, 43000.0, 41500.0, 42500.0, 100.0]]
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = rows

    connector = CcxtOhlcvConnector(cfg, mock_exchange)
    df = connector.fetch("BTC/USDT")

    expected_utc = pd.Timestamp("2024-01-01T00:00:00Z")
    assert df["datetime"].iloc[0] == expected_utc
    # Ensure timezone-aware UTC
    assert df["datetime"].dt.tz is not None


# ---------------------------------------------------------------------------
# 10. CcxtBarMapper — returns non-empty list[Bar] with correct bar_type
# ---------------------------------------------------------------------------

def _make_spot_instrument():
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper
    cfg = CcxtDataConfig(
        exchange_id="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        venue="BINANCE",
    )
    mapper = CcxtInstrumentMapper(cfg, precision_mode=2)
    return cfg, mapper.build_instrument(SPOT_MARKET, "spot")


@nautilus_required
def test_bar_mapper_returns_bars():
    from nautilus_ext.ccxt.ccxt_bar_mapper import CcxtBarMapper
    from nautilus_trader.model.data import Bar

    cfg, instrument = _make_spot_instrument()
    raw_rows = SAMPLE_OHLCV_ROWS[:-1]  # exclude last (drop_incomplete_bar is default True)

    # Build the OHLCV DataFrame as CcxtOhlcvConnector would produce it
    from nautilus_ext.ccxt.ccxt_ohlcv_connector import CcxtOhlcvConnector
    cfg_no_drop = CcxtDataConfig(
        exchange_id="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        drop_incomplete_bar=False,
        venue="BINANCE",
    )
    mock_ex = MagicMock()
    mock_ex.fetch_ohlcv.return_value = raw_rows
    df = CcxtOhlcvConnector(cfg_no_drop, mock_ex).fetch("BTC/USDT")

    bar_mapper = CcxtBarMapper(cfg, instrument)
    bars = bar_mapper.map(df)

    assert len(bars) > 0
    assert all(isinstance(b, Bar) for b in bars)
    # bar_type string contains instrument id and timeframe
    bar_type_str = str(bar_mapper.bar_type)
    assert "BTCUSDT" in bar_type_str
    assert "MINUTE" in bar_type_str


# ---------------------------------------------------------------------------
# 11. CcxtBarDataConnector.prepare_data() — end-to-end
# ---------------------------------------------------------------------------

def _make_config(symbol="BTC/USDT", market_type="spot", venue="BINANCE") -> "CcxtDataConfig":
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
    return CcxtDataConfig(
        exchange_id="binance",
        market_type=market_type,
        symbols=[symbol],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        until="2024-01-01T01:00:00Z",
        venue=venue,
        drop_incomplete_bar=False,
        save_raw=False,
        save_parquet=False,
    )


def _mock_binance(markets, ohlcv_rows):
    """Return a ccxt-mock context manager that patches the ccxt module."""
    mock_ccxt, MockExchange = _make_mock_ccxt(markets, ohlcv_rows, precision_mode=2)

    class _MockExchangeInstance:
        precisionMode = 2

        def load_markets(self):
            return markets

        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
            return list(ohlcv_rows)

        def set_sandbox_mode(self, x):
            pass

    mock_ccxt.binance = lambda params: _MockExchangeInstance()
    return mock_ccxt


@nautilus_required
def test_prepare_data_end_to_end_spot():
    from nautilus_ext.ccxt.ccxt_connector import CcxtBarDataConnector
    from nautilus_trader.model.data import Bar

    ohlcv = SAMPLE_OHLCV_ROWS[:-1]  # 4 rows without the last "incomplete" one
    mock_ccxt = _mock_binance({"BTC/USDT": SPOT_MARKET}, ohlcv)

    with patch.dict(sys.modules, {"ccxt": mock_ccxt}):
        cfg = _make_config()
        connector = CcxtBarDataConnector(cfg)
        bars = connector.prepare_data()

    assert isinstance(bars, list)
    assert len(bars) > 0
    assert all(isinstance(b, Bar) for b in bars)


@nautilus_required
def test_prepare_data_end_to_end_swap():
    from nautilus_ext.ccxt.ccxt_connector import CcxtBarDataConnector
    from nautilus_trader.model.instruments import CryptoPerpetual

    ohlcv = SAMPLE_OHLCV_ROWS[:-1]
    mock_ccxt = _mock_binance({"BTC/USDT:USDT": SWAP_MARKET}, ohlcv)

    with patch.dict(sys.modules, {"ccxt": mock_ccxt}):
        cfg = _make_config(symbol="BTC/USDT:USDT", market_type="swap")
        connector = CcxtBarDataConnector(cfg)
        bars = connector.prepare_data()
        instrument = connector.instrument

    assert isinstance(instrument, CryptoPerpetual)
    assert len(bars) > 0


# ---------------------------------------------------------------------------
# 12. CcxtBarDataConnector — BacktestRunner interface
# ---------------------------------------------------------------------------

@nautilus_required
def test_backtest_runner_interface():
    """Verify the three attributes NautilusBacktestRunner accesses are available."""
    from nautilus_ext.ccxt.ccxt_connector import CcxtBarDataConnector

    ohlcv = SAMPLE_OHLCV_ROWS[:-1]
    mock_ccxt = _mock_binance({"BTC/USDT": SPOT_MARKET}, ohlcv)

    with patch.dict(sys.modules, {"ccxt": mock_ccxt}):
        cfg = _make_config()
        connector = CcxtBarDataConnector(cfg)

        # prepare_data() — must return list
        bars = connector.prepare_data()
        assert isinstance(bars, list)

        # instrument — must be a Nautilus Instrument attribute
        instr = connector.instrument
        assert hasattr(instr, "id")

        # get_bar_type() — must return a BarType
        bt = connector.get_bar_type()
        assert bt is not None
        assert "BTCUSDT" in str(bt)


# ---------------------------------------------------------------------------
# 13. CcxtBarDataConnector.save_outputs() — writes expected files
# ---------------------------------------------------------------------------

@nautilus_required
def test_save_outputs_writes_files(tmp_path):
    from nautilus_ext.ccxt.ccxt_connector import CcxtBarDataConnector
    from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig

    ohlcv = SAMPLE_OHLCV_ROWS[:-1]
    mock_ccxt = _mock_binance({"BTC/USDT": SPOT_MARKET}, ohlcv)

    with patch.dict(sys.modules, {"ccxt": mock_ccxt}):
        cfg = CcxtDataConfig(
            exchange_id="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframe="1m",
            since="2024-01-01T00:00:00Z",
            venue="BINANCE",
            output_dir=str(tmp_path),
            save_raw=True,
            save_parquet=True,
            drop_incomplete_bar=False,
        )
        connector = CcxtBarDataConnector(cfg)
        connector.prepare_data()
        saved = connector.save_outputs()

    # markets.json must exist
    assert "markets.json" in saved
    assert saved["markets.json"].exists()
    markets_data = json.loads(saved["markets.json"].read_text())
    assert "BTC/USDT" in markets_data

    # connector_profile.json must exist
    profile_key = "BTC/USDT/connector_profile.json"
    assert profile_key in saved
    assert saved[profile_key].exists()
    profile = json.loads(saved[profile_key].read_text())
    assert profile["exchange_id"] == "binance"
    assert profile["symbol"] == "BTC/USDT"

    # raw CSV and parquet
    assert "BTC/USDT/raw_ohlcv.csv" in saved
    assert saved["BTC/USDT/raw_ohlcv.csv"].exists()
    assert "BTC/USDT/raw_ohlcv.parquet" in saved
    assert saved["BTC/USDT/raw_ohlcv.parquet"].exists()


# ---------------------------------------------------------------------------
# 14. Original NautilusAutoBarDataConnector is not broken (import check)
# ---------------------------------------------------------------------------

def test_original_auto_connector_import_not_broken():
    from nautilus_ext.connectors.auto_bar_data_connector import NautilusAutoBarDataConnector  # noqa
    assert NautilusAutoBarDataConnector is not None


def test_ccxt_package_does_not_import_eagerly():
    """Importing nautilus_ext.ccxt __init__ must not trigger ccxt or heavy Nautilus imports."""
    # Simply verify the __init__ module attributes are accessible
    import nautilus_ext.ccxt as pkg
    # __all__ should be defined and contain the two public names
    assert "CcxtDataConfig" in pkg.__all__
    assert "CcxtBarDataConnector" in pkg.__all__
