"""Binance Vision historical market data source adapter.

Imports OHLCV bars from Binance Vision archive into StandardBar schema.
Supports spot, futures_um (USD-M), and futures_cm (COIN-M) markets.
"""
from __future__ import annotations

import io
import warnings
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, TYPE_CHECKING
from urllib.request import urlopen
from urllib.error import HTTPError

if TYPE_CHECKING:
    import polars as pl

# Market and frequency types
Market = Literal["spot", "futures_um", "futures_cm"]
Frequency = Literal["monthly", "daily"]


@dataclass(frozen=True)
class StandardBar:
    """Standard OHLCV bar schema for normalized market data."""
    ts: datetime
    exchange: str
    venue_type: str
    symbol: str
    instrument_id: str
    bar_type: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int
    taker_buy_volume: float
    taker_buy_quote_volume: float
    source: str
    ingested_at: datetime


def build_binance_vision_kline_url(
    market: Market,
    symbol: str,
    interval: str,
    frequency: Frequency,
    date: str,
) -> str:
    """Build Binance Vision kline download URL.

    Parameters
    ----------
    market : Market
        "spot", "futures_um", or "futures_cm"
    symbol : str
        Trading pair symbol (e.g., "BTCUSDT")
    interval : str
        Bar interval (e.g., "1m", "5m", "1h", "1d")
    frequency : Frequency
        "monthly" or "daily"
    date : str
        YYYY-MM (monthly) or YYYY-MM-DD (daily) format

    Returns
    -------
    str
        Full URL to download ZIP file

    Raises
    ------
    ValueError
        If market type is invalid or date format is wrong
    """
    if market not in ("spot", "futures_um", "futures_cm"):
        raise ValueError(f"Invalid market: {market}. Must be spot|futures_um|futures_cm")

    base_url = "https://data.binance.vision/data/spot"
    if market == "futures_um":
        base_url = "https://data.binance.vision/data/futures/um"
    elif market == "futures_cm":
        base_url = "https://data.binance.vision/data/futures/cm"

    if frequency == "monthly":
        if len(date) != 7 or date[4] != "-":
            raise ValueError(f"Monthly date must be YYYY-MM format, got {date}")
        url = f"{base_url}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{date}.zip"
    else:  # daily
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            raise ValueError(f"Daily date must be YYYY-MM-DD format, got {date}")
        url = f"{base_url}/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{date}.zip"

    return url


def read_binance_kline_zip(
    url_or_bytes: str | bytes,
    *,
    timeout: int = 30,
) -> list[dict]:
    """Read Binance kline CSV from ZIP file.

    Parameters
    ----------
    url_or_bytes : str | bytes
        URL string to download, or raw ZIP bytes (for testing)
    timeout : int
        Download timeout in seconds (ignored for bytes input)

    Returns
    -------
    list[dict]
        List of dicts with Binance kline fields

    Raises
    ------
    HTTPError
        If download fails with HTTP error
    IOError
        If ZIP reading fails
    """
    if isinstance(url_or_bytes, str):
        try:
            response = urlopen(url_or_bytes, timeout=timeout)
            zip_bytes = response.read()
        except HTTPError as e:
            raise HTTPError(
                url_or_bytes, e.code,
                f"Download failed: {e.code} at {url_or_bytes}",
                e.hdrs, e.fp
            ) from e
    else:
        zip_bytes = url_or_bytes

    rows = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # Find CSV file in ZIP
            csv_files = [f for f in zf.namelist() if f.endswith(".csv")]
            if not csv_files:
                raise ValueError("No CSV file found in ZIP")
            if len(csv_files) > 1:
                warnings.warn(f"Multiple CSV files in ZIP, using first: {csv_files[0]}")

            csv_name = csv_files[0]
            with zf.open(csv_name) as f:
                text_data = f.read().decode("utf-8")
                for line in text_data.strip().split("\n"):
                    if not line:
                        continue
                    fields = line.strip().split(",")
                    if len(fields) >= 12:
                        try:
                            rows.append({
                                "open_time": int(fields[0]),
                                "open": float(fields[1]),
                                "high": float(fields[2]),
                                "low": float(fields[3]),
                                "close": float(fields[4]),
                                "volume": float(fields[5]),
                                "close_time": int(fields[6]),
                                "quote_asset_volume": float(fields[7]),
                                "number_of_trades": int(fields[8]),
                                "taker_buy_base_asset_volume": float(fields[9]),
                                "taker_buy_quote_asset_volume": float(fields[10]),
                            })
                        except (ValueError, IndexError) as e:
                            warnings.warn(f"Failed to parse line: {line[:50]}... ({e})")
    except zipfile.BadZipFile as e:
        raise IOError(f"Invalid ZIP format") from e

    return rows


def _detect_timestamp_unit(open_time: int) -> str:
    """Detect if timestamp is in milliseconds or microseconds.

    Binance typically uses milliseconds, but some feeds may use microseconds.
    Heuristic: timestamps > 1e13 are microseconds (after year 5138 in ms).
    """
    if open_time > 1e13:
        return "us"
    return "ms"


def normalize_binance_kline(
    rows: list[dict],
    *,
    market: Market,
    symbol: str,
    interval: str,
    venue_type: str | None = None,
) -> "pl.DataFrame":
    """Normalize Binance kline CSV to StandardBar schema.

    Parameters
    ----------
    rows : list[dict]
        Raw kline rows from read_binance_kline_zip()
    market : Market
        "spot", "futures_um", or "futures_cm"
    symbol : str
        Trading pair symbol (e.g., "BTCUSDT")
    interval : str
        Bar interval (e.g., "1m", "5m", "1h", "1d")
    venue_type : str | None
        Override venue_type (defaults to market name)

    Returns
    -------
    pl.DataFrame
        Normalized bars with StandardBar schema

    Raises
    ------
    ValueError
        If rows are empty or have invalid data
    """
    if not rows:
        raise ValueError("No rows to normalize")

    import polars as pl  # noqa: PLC0415

    venue = venue_type or market
    ingested_at = datetime.utcnow()

    # Detect timestamp unit from first row
    unit = _detect_timestamp_unit(rows[0]["open_time"])
    factor = 1000 if unit == "ms" else 1_000_000

    normalized_rows = []
    for row in rows:
        # Convert timestamp: Binance open_time is in ms or us since epoch
        ts_seconds = row["open_time"] / factor
        ts = datetime.utcfromtimestamp(ts_seconds)

        normalized_rows.append({
            "ts": ts,
            "exchange": "BINANCE",
            "venue_type": venue,
            "symbol": symbol,
            "instrument_id": symbol,
            "bar_type": interval,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "quote_volume": float(row["quote_asset_volume"]),
            "trade_count": int(row["number_of_trades"]),
            "taker_buy_volume": float(row["taker_buy_base_asset_volume"]),
            "taker_buy_quote_volume": float(row["taker_buy_quote_asset_volume"]),
            "source": "binance_vision",
            "ingested_at": ingested_at,
        })

    # Create DataFrame and validate
    df = pl.DataFrame(normalized_rows, schema={
        "ts": pl.Datetime("us"),
        "exchange": pl.Utf8,
        "venue_type": pl.Utf8,
        "symbol": pl.Utf8,
        "instrument_id": pl.Utf8,
        "bar_type": pl.Utf8,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
        "quote_volume": pl.Float64,
        "trade_count": pl.Int64,
        "taker_buy_volume": pl.Float64,
        "taker_buy_quote_volume": pl.Float64,
        "source": pl.Utf8,
        "ingested_at": pl.Datetime("us"),
    })

    # Validate OHLC
    invalid_high_low = df.filter(pl.col("high") < pl.col("low"))
    if invalid_high_low.height > 0:
        raise ValueError(
            f"Invalid OHLC: high < low in {invalid_high_low.height} rows"
        )

    # Ensure monotonic increasing timestamps
    if not df["ts"].is_sorted():
        raise ValueError("Timestamps are not monotonically increasing")

    return df.sort("ts")


class BinanceVisionImporter:
    """High-level importer for Binance Vision historical data."""

    def __init__(self, *, timeout: int = 30):
        """Initialize importer.

        Parameters
        ----------
        timeout : int
            Download timeout in seconds
        """
        self.timeout = timeout

    def import_period(
        self,
        market: Market,
        symbol: str,
        interval: str,
        frequency: Frequency,
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """Import bars for a date range.

        Parameters
        ----------
        market : Market
            "spot", "futures_um", or "futures_cm"
        symbol : str
            Trading pair symbol (e.g., "BTCUSDT")
        interval : str
            Bar interval (e.g., "1m", "5m", "1h", "1d")
        frequency : Frequency
            "monthly" or "daily"
        start_date : str
            YYYY-MM (monthly) or YYYY-MM-DD (daily)
        end_date : str
            YYYY-MM (monthly) or YYYY-MM-DD (daily)

        Returns
        -------
        pl.DataFrame
            Combined bars from all dates in range

        Raises
        ------
        ValueError
            If date range is invalid
        """
        dates = self._generate_dates(frequency, start_date, end_date)
        if not dates:
            raise ValueError(f"No dates generated for range {start_date} to {end_date}")

        all_dfs = []
        failed_dates = []

        for date in dates:
            try:
                url = build_binance_vision_kline_url(market, symbol, interval, frequency, date)
                rows = read_binance_kline_zip(url, timeout=self.timeout)
                if rows:
                    df = normalize_binance_kline(rows, market=market, symbol=symbol, interval=interval)
                    all_dfs.append(df)
            except (HTTPError, IOError, ValueError) as e:
                failed_dates.append((date, str(e)))

        if not all_dfs:
            raise ValueError(f"Failed to import any data. Failed dates: {failed_dates}")

        if failed_dates:
            for date, err in failed_dates[:3]:
                print(f"Warning: failed to import {date}: {err}")
            if len(failed_dates) > 3:
                print(f"... and {len(failed_dates) - 3} more")

        import polars as pl  # noqa: PLC0415
        return pl.concat(all_dfs).sort("ts")

    @staticmethod
    def _generate_dates(
        frequency: Frequency,
        start_date: str,
        end_date: str,
    ) -> list[str]:
        """Generate date range for downloads.

        Parameters
        ----------
        frequency : Frequency
            "monthly" or "daily"
        start_date : str
            Start date in appropriate format
        end_date : str
            End date in appropriate format

        Returns
        -------
        list[str]
            List of dates in same format as inputs
        """
        dates = []

        if frequency == "monthly":
            start = datetime.strptime(start_date, "%Y-%m")
            end = datetime.strptime(end_date, "%Y-%m")
            current = start
            while current <= end:
                dates.append(current.strftime("%Y-%m"))
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
        else:  # daily
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            current = start
            while current <= end:
                dates.append(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)

        return dates


__all__ = [
    "BinanceVisionImporter",
    "build_binance_vision_kline_url",
    "read_binance_kline_zip",
    "normalize_binance_kline",
    "Market",
    "Frequency",
]