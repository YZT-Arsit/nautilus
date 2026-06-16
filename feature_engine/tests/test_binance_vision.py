"""Tests for Binance Vision data source adapter."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime

import polars as pl
import pytest

from feature_engine.data_sources.binance_vision import (
    BinanceVisionImporter,
    build_binance_vision_kline_url,
    normalize_binance_kline,
    read_binance_kline_zip,
)


class TestBuildUrl:
    """Test URL construction."""

    def test_spot_monthly_url(self) -> None:
        url = build_binance_vision_kline_url(
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            frequency="monthly",
            date="2024-01",
        )
        assert url == (
            "https://data.binance.vision/data/spot/monthly/klines/"
            "BTCUSDT/1m/BTCUSDT-1m-2024-01.zip"
        )

    def test_spot_daily_url(self) -> None:
        url = build_binance_vision_kline_url(
            market="spot",
            symbol="ETHUSDT",
            interval="5m",
            frequency="daily",
            date="2024-06-15",
        )
        assert url == (
            "https://data.binance.vision/data/spot/daily/klines/"
            "ETHUSDT/5m/ETHUSDT-5m-2024-06-15.zip"
        )

    def test_futures_um_url(self) -> None:
        url = build_binance_vision_kline_url(
            market="futures_um",
            symbol="BTCUSDT",
            interval="1h",
            frequency="daily",
            date="2024-06-15",
        )
        assert "data.binance.vision/data/futures/um" in url
        assert "BTCUSDT-1h-2024-06-15.zip" in url

    def test_futures_cm_url(self) -> None:
        url = build_binance_vision_kline_url(
            market="futures_cm",
            symbol="BTCUSD",
            interval="4h",
            frequency="monthly",
            date="2024-06",
        )
        assert "data.binance.vision/data/futures/cm" in url
        assert "BTCUSD-4h-2024-06.zip" in url

    def test_invalid_market(self) -> None:
        with pytest.raises(ValueError, match="Invalid market"):
            build_binance_vision_kline_url(
                market="invalid",  # type: ignore
                symbol="BTCUSDT",
                interval="1m",
                frequency="monthly",
                date="2024-01",
            )

    def test_invalid_monthly_date(self) -> None:
        with pytest.raises(ValueError, match="Monthly date must be YYYY-MM"):
            build_binance_vision_kline_url(
                market="spot",
                symbol="BTCUSDT",
                interval="1m",
                frequency="monthly",
                date="2024-01-01",  # Should be YYYY-MM
            )

    def test_invalid_daily_date(self) -> None:
        with pytest.raises(ValueError, match="Daily date must be YYYY-MM-DD"):
            build_binance_vision_kline_url(
                market="spot",
                symbol="BTCUSDT",
                interval="1m",
                frequency="daily",
                date="2024-01",  # Should be YYYY-MM-DD
            )


def _create_mock_binance_csv_zip(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    timestamp_unit: str = "ms",
) -> bytes:
    """Create a mock Binance CSV inside a ZIP file.

    Parameters
    ----------
    symbol : str
        Trading pair symbol
    interval : str
        Bar interval
    timestamp_unit : str
        "ms" for milliseconds, "us" for microseconds

    Returns
    -------
    bytes
        ZIP file contents
    """
    # Binance kline CSV format: no header, 12 fields
    # open_time, open, high, low, close, volume, close_time,
    # quote_asset_volume, number_of_trades,
    # taker_buy_base_asset_volume, taker_buy_quote_asset_volume, ignore
    if timestamp_unit == "ms":
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        base_ts = 1704067200000
    else:  # microseconds
        base_ts = 1704067200000000

    rows = []
    for i in range(5):
        ts = base_ts + (i * 60000 if timestamp_unit == "ms" else i * 60000000)
        close_ts = ts + (59999 if timestamp_unit == "ms" else 59999000)

        row = [
            str(ts),  # open_time
            "40000.50",  # open
            f"40{i:03d}.99",  # high
            "39999.00",  # low
            f"40{i:03d}.50",  # close
            "100.5",  # volume
            str(close_ts),  # close_time
            "4000000.00",  # quote_asset_volume
            "1234",  # number_of_trades
            "50.25",  # taker_buy_base_asset_volume
            "2000000.00",  # taker_buy_quote_asset_volume
            "0",  # ignore
        ]
        rows.append(",".join(row))

    csv_content = "\n".join(rows)

    # Create ZIP file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        csv_name = f"{symbol}-{interval}-2024-01-01.csv"
        zf.writestr(csv_name, csv_content)

    return zip_buffer.getvalue()


class TestReadBinanceKlineZip:
    """Test ZIP and CSV reading."""

    def test_read_from_bytes_milliseconds(self) -> None:
        zip_bytes = _create_mock_binance_csv_zip(timestamp_unit="ms")
        rows = read_binance_kline_zip(zip_bytes)

        assert len(rows) == 5
        assert rows[0]["open_time"] == 1704067200000
        assert rows[0]["open"] == 40000.50
        assert rows[0]["close"] == 40000.50
        assert rows[0]["volume"] == 100.5
        assert rows[0]["number_of_trades"] == 1234

    def test_read_from_bytes_microseconds(self) -> None:
        zip_bytes = _create_mock_binance_csv_zip(timestamp_unit="us")
        rows = read_binance_kline_zip(zip_bytes)

        assert len(rows) == 5
        assert rows[0]["open_time"] == 1704067200000000
        assert rows[0]["volume"] == 100.5

    def test_invalid_zip_format(self) -> None:
        with pytest.raises(IOError, match="Invalid ZIP"):
            read_binance_kline_zip(b"not a zip file")

    def test_empty_csv(self) -> None:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("BTCUSDT-1m-2024-01-01.csv", "")

        rows = read_binance_kline_zip(zip_buffer.getvalue())
        assert rows == []

    def test_no_csv_in_zip(self) -> None:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("readme.txt", "no csv here")

        with pytest.raises(ValueError, match="No CSV file found"):
            read_binance_kline_zip(zip_buffer.getvalue())


class TestNormalizeBinanceKline:
    """Test normalization to StandardBar schema."""

    def test_normalize_milliseconds(self) -> None:
        zip_bytes = _create_mock_binance_csv_zip(timestamp_unit="ms")
        rows = read_binance_kline_zip(zip_bytes)

        df = normalize_binance_kline(
            rows,
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
        )

        assert df.height == 5
        assert set(df.columns) == {
            "ts", "exchange", "venue_type", "symbol", "instrument_id",
            "bar_type", "open", "high", "low", "close", "volume",
            "quote_volume", "trade_count", "taker_buy_volume",
            "taker_buy_quote_volume", "source", "ingested_at",
        }

        # Check first bar
        first = df.row(0, named=True)
        assert first["exchange"] == "BINANCE"
        assert first["venue_type"] == "spot"
        assert first["symbol"] == "BTCUSDT"
        assert first["instrument_id"] == "BTCUSDT"
        assert first["bar_type"] == "1m"
        assert first["source"] == "binance_vision"
        assert first["open"] == 40000.50
        assert first["high"] == 40000.99
        assert first["low"] == 39999.00
        assert first["close"] == 40000.50
        assert first["volume"] == 100.5
        assert first["trade_count"] == 1234

    def test_normalize_microseconds(self) -> None:
        zip_bytes = _create_mock_binance_csv_zip(timestamp_unit="us")
        rows = read_binance_kline_zip(zip_bytes)

        df = normalize_binance_kline(
            rows,
            market="futures_um",
            symbol="ETHUSDT",
            interval="5m",
        )

        assert df.height == 5
        assert df.row(0, named=True)["venue_type"] == "futures_um"

    def test_normalize_custom_venue_type(self) -> None:
        zip_bytes = _create_mock_binance_csv_zip()
        rows = read_binance_kline_zip(zip_bytes)

        df = normalize_binance_kline(
            rows,
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            venue_type="custom_venue",
        )

        assert df.row(0, named=True)["venue_type"] == "custom_venue"

    def test_normalize_ingested_at_timestamp(self) -> None:
        zip_bytes = _create_mock_binance_csv_zip()
        rows = read_binance_kline_zip(zip_bytes)
        before = datetime.utcnow()

        df = normalize_binance_kline(rows, market="spot", symbol="BTCUSDT", interval="1m")
        after = datetime.utcnow()

        # ingested_at should be approximately now
        ingested = df.row(0, named=True)["ingested_at"]
        assert before <= ingested <= after

    def test_normalize_empty_rows(self) -> None:
        with pytest.raises(ValueError, match="No rows"):
            normalize_binance_kline([], market="spot", symbol="BTCUSDT", interval="1m")

    def test_normalize_invalid_high_low(self) -> None:
        # Create row with high < low
        rows = [{
            "open_time": 1704067200000,
            "open": 100.0,
            "high": 90.0,  # Invalid: should be >= low
            "low": 95.0,
            "close": 98.0,
            "volume": 100.0,
            "close_time": 1704067259999,
            "quote_asset_volume": 10000.0,
            "number_of_trades": 100,
            "taker_buy_base_asset_volume": 50.0,
            "taker_buy_quote_asset_volume": 5000.0,
        }]

        with pytest.raises(ValueError, match="Invalid OHLC"):
            normalize_binance_kline(rows, market="spot", symbol="BTCUSDT", interval="1m")

    def test_normalize_monotonic_timestamps(self) -> None:
        # Create rows with decreasing timestamps
        rows = [
            {
                "open_time": 1704067200000 + i,
                "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
                "volume": 100.0, "close_time": 1704067200000 + i + 1000,
                "quote_asset_volume": 10000.0, "number_of_trades": 100,
                "taker_buy_base_asset_volume": 50.0, "taker_buy_quote_asset_volume": 5000.0,
            }
            for i in [0, 1000, 500]  # Non-monotonic
        ]

        with pytest.raises(ValueError, match="not monotonically increasing"):
            normalize_binance_kline(rows, market="spot", symbol="BTCUSDT", interval="1m")


class TestBinanceVisionImporter:
    """Test high-level importer."""

    def test_generate_daily_dates(self) -> None:
        dates = BinanceVisionImporter._generate_dates(
            "daily",
            "2024-01-01",
            "2024-01-05",
        )
        assert dates == ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    def test_generate_monthly_dates(self) -> None:
        dates = BinanceVisionImporter._generate_dates(
            "monthly",
            "2024-01",
            "2024-03",
        )
        assert dates == ["2024-01", "2024-02", "2024-03"]

    def test_generate_monthly_dates_year_boundary(self) -> None:
        dates = BinanceVisionImporter._generate_dates(
            "monthly",
            "2023-11",
            "2024-02",
        )
        assert dates == ["2023-11", "2023-12", "2024-01", "2024-02"]

    def test_generate_empty_range(self) -> None:
        dates = BinanceVisionImporter._generate_dates(
            "daily",
            "2024-01-05",
            "2024-01-01",  # end < start
        )
        assert dates == []

    def test_import_period_error_handling(self) -> None:
        """Test that importer handles network errors gracefully."""
        importer = BinanceVisionImporter()

        # Try to import from non-existent dates
        # (this will fail with HTTP 404, which is expected)
        with pytest.raises(ValueError, match="Failed to import any data"):
            importer.import_period(
                market="spot",
                symbol="BTCUSDT",
                interval="1m",
                frequency="daily",
                start_date="1970-01-01",  # Very old, unlikely to exist
                end_date="1970-01-02",
            )


class TestSchemaValidation:
    """Test StandardBar schema compliance."""

    def test_output_schema_types(self) -> None:
        """Verify all output columns have correct types."""
        zip_bytes = _create_mock_binance_csv_zip()
        rows = read_binance_kline_zip(zip_bytes)

        df = normalize_binance_kline(rows, market="spot", symbol="BTCUSDT", interval="1m")

        schema = df.schema
        assert schema["ts"] == pl.Datetime("us")
        assert schema["exchange"] == pl.Utf8
        assert schema["venue_type"] == pl.Utf8
        assert schema["symbol"] == pl.Utf8
        assert schema["instrument_id"] == pl.Utf8
        assert schema["bar_type"] == pl.Utf8
        assert schema["open"] == pl.Float64
        assert schema["high"] == pl.Float64
        assert schema["low"] == pl.Float64
        assert schema["close"] == pl.Float64
        assert schema["volume"] == pl.Float64
        assert schema["quote_volume"] == pl.Float64
        assert schema["trade_count"] == pl.Int64
        assert schema["taker_buy_volume"] == pl.Float64
        assert schema["taker_buy_quote_volume"] == pl.Float64
        assert schema["source"] == pl.Utf8
        assert schema["ingested_at"] == pl.Datetime("us")

    def test_timestamp_precision(self) -> None:
        """Verify timestamps are stored with microsecond precision."""
        zip_bytes = _create_mock_binance_csv_zip()
        rows = read_binance_kline_zip(zip_bytes)

        df = normalize_binance_kline(rows, market="spot", symbol="BTCUSDT", interval="1m")

        # All timestamps should be datetime objects with microsecond precision
        for ts in df["ts"]:
            assert isinstance(ts, datetime)