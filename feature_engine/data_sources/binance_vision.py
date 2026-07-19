"""Binance Vision historical market data source adapter.

Imports OHLCV bars from Binance Vision archive into StandardBar schema.
Supports spot, futures_um (USD-M), and futures_cm (COIN-M) markets.
"""
from __future__ import annotations

import io
import json
import warnings
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, TYPE_CHECKING
from urllib.request import urlopen
from urllib.parse import urlencode
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


@dataclass(frozen=True)
class StandardTrade:
    """Standard trade (aggTrades) schema for normalized market data.

    Trade data has no ``bar_type``; partitioning uses ``data_type`` instead.
    ``side`` is the aggressor side derived from ``is_buyer_maker`` (Binance:
    ``is_buyer_maker=True`` -> aggressive SELL, ``False`` -> aggressive BUY).
    """
    ts: datetime
    exchange: str
    venue_type: str
    symbol: str
    instrument_id: str
    agg_trade_id: int
    price: float
    quantity: float
    quote_quantity: float
    first_trade_id: int
    last_trade_id: int
    is_buyer_maker: bool
    side: str
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


# ===========================================================================
# aggTrades (trade data) — parallel to the kline path above
# ===========================================================================

def build_binance_vision_aggtrades_url(
    market: Market,
    symbol: str,
    frequency: Frequency,
    date: str,
) -> str:
    """Build a Binance Vision aggTrades download URL.

    Example (spot daily)::

        https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-06-01.zip
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
        return f"{base_url}/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{date}.zip"
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        raise ValueError(f"Daily date must be YYYY-MM-DD format, got {date}")
    return f"{base_url}/daily/aggTrades/{symbol}/{symbol}-aggTrades-{date}.zip"


def build_binance_vision_funding_url(symbol: str, date: str) -> str:
    """Return the USD-M monthly funding-rate archive URL."""
    if len(date) != 7 or date[4] != "-":
        raise ValueError(f"Monthly date must be YYYY-MM format, got {date}")
    return (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        f"{symbol}/{symbol}-fundingRate-{date}.zip"
    )


def read_binance_funding_zip(
    url_or_bytes: str | bytes, *, timeout: int = 30,
) -> list[dict]:
    """Read Binance Vision's funding CSV into neutral rows."""
    if isinstance(url_or_bytes, str):
        try:
            response = urlopen(url_or_bytes, timeout=timeout)
            zip_bytes = response.read()
        except HTTPError as e:
            raise HTTPError(
                url_or_bytes, e.code, f"Download failed: {e.code} at {url_or_bytes}",
                e.hdrs, e.fp,
            ) from e
    else:
        zip_bytes = url_or_bytes
    rows: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_files = [name for name in zf.namelist() if name.endswith(".csv")]
            if not csv_files:
                raise ValueError("No CSV file found in ZIP")
            lines = zf.read(csv_files[0]).decode("utf-8").strip().splitlines()
            for line in lines:
                fields = line.strip().split(",")
                if len(fields) < 3:
                    continue
                try:
                    rows.append({
                        "calc_time": int(fields[0]),
                        "funding_interval_hours": int(fields[1]),
                        "funding_rate": float(fields[2]),
                    })
                except ValueError:  # header or malformed row
                    continue
    except zipfile.BadZipFile as e:
        raise IOError("Invalid ZIP format") from e
    return rows


def normalize_binance_funding(rows: list[dict], *, symbol: str) -> "pl.DataFrame":
    """Normalize archived USD-M settlements to the funding-rate schema."""
    if not rows:
        raise ValueError("No funding rows to normalize")
    import polars as pl  # noqa: PLC0415

    ingested_at = datetime.utcnow()
    normalized = [{
        "ts": datetime.utcfromtimestamp(row["calc_time"] / 1000),
        "exchange": "BINANCE",
        "venue_type": "futures_um",
        "symbol": symbol,
        "instrument_id": f"{symbol}-PERP.BINANCE",
        "funding_rate": float(row["funding_rate"]),
        "funding_interval_hours": int(row["funding_interval_hours"]),
        "source": "binance_vision_funding_rate",
        "ingested_at": ingested_at,
    } for row in rows]
    return pl.DataFrame(normalized).sort("ts")


def read_binance_funding_api(
    symbol: str, start_date: str, end_date: str, *, timeout: int = 30,
) -> list[dict]:
    """Fetch the not-yet-archived USD-M funding tail from the public REST API."""
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        + timedelta(days=1) - timedelta(milliseconds=1)
    )
    params = urlencode({
        "symbol": symbol,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "limit": 1000,
    })
    with urlopen(f"https://fapi.binance.com/fapi/v1/fundingRate?{params}", timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [{
        "calc_time": int(row["fundingTime"]),
        "funding_interval_hours": 8,
        "funding_rate": float(row["fundingRate"]),
    } for row in payload]


def _parse_bool(value: str) -> bool:
    """Parse a Binance CSV boolean ('true'/'false', case-insensitive)."""
    return str(value).strip().lower() in ("true", "1")


def read_binance_aggtrades_zip(
    url_or_bytes: str | bytes,
    *,
    timeout: int = 30,
) -> list[dict]:
    """Read Binance aggTrades CSV from a ZIP (URL or raw bytes).

    Binance aggTrades CSV columns (no guaranteed header)::

        aggTradeId, price, quantity, firstTradeId, lastTradeId,
        transactTime(ms), isBuyerMaker[, isBestMatch]

    Spot files carry the trailing ``isBestMatch``; futures files omit it. A
    header row (if present) is detected and skipped (non-integer first field).
    """
    if isinstance(url_or_bytes, str):
        try:
            response = urlopen(url_or_bytes, timeout=timeout)
            zip_bytes = response.read()
        except HTTPError as e:
            raise HTTPError(
                url_or_bytes, e.code,
                f"Download failed: {e.code} at {url_or_bytes}",
                e.hdrs, e.fp,
            ) from e
    else:
        zip_bytes = url_or_bytes

    rows: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_files = [f for f in zf.namelist() if f.endswith(".csv")]
            if not csv_files:
                raise ValueError("No CSV file found in ZIP")
            if len(csv_files) > 1:
                warnings.warn(f"Multiple CSV files in ZIP, using first: {csv_files[0]}")
            with zf.open(csv_files[0]) as f:
                text_data = f.read().decode("utf-8")
                for line in text_data.strip().split("\n"):
                    if not line:
                        continue
                    fields = line.strip().split(",")
                    if len(fields) < 7:
                        continue
                    try:
                        agg_id = int(fields[0])
                    except ValueError:
                        # header row or malformed line — skip
                        continue
                    try:
                        rows.append({
                            "agg_trade_id": agg_id,
                            "price": float(fields[1]),
                            "quantity": float(fields[2]),
                            "first_trade_id": int(fields[3]),
                            "last_trade_id": int(fields[4]),
                            "timestamp": int(fields[5]),
                            "is_buyer_maker": _parse_bool(fields[6]),
                            "is_best_match": _parse_bool(fields[7]) if len(fields) > 7 else None,
                        })
                    except (ValueError, IndexError) as e:
                        warnings.warn(f"Failed to parse line: {line[:50]}... ({e})")
    except zipfile.BadZipFile as e:
        raise IOError("Invalid ZIP format") from e

    return rows


def normalize_binance_aggtrades(
    rows: list[dict],
    *,
    market: Market,
    symbol: str,
    venue_type: str | None = None,
) -> "pl.DataFrame":
    """Normalize Binance aggTrades rows to the StandardTrade schema (polars).

    ``quote_quantity = price * quantity``; ``side`` is derived from
    ``is_buyer_maker`` (True -> SELL, False -> BUY); ``ts`` is a UTC
    ``Datetime("us")``. Rows are validated to have monotonically increasing ``ts``.
    """
    if not rows:
        raise ValueError("No rows to normalize")

    import polars as pl  # noqa: PLC0415

    venue = venue_type or market
    ingested_at = datetime.utcnow()
    unit = _detect_timestamp_unit(rows[0]["timestamp"])
    factor = 1000 if unit == "ms" else 1_000_000

    normalized_rows = []
    for row in rows:
        ts = datetime.utcfromtimestamp(row["timestamp"] / factor)
        price = float(row["price"])
        quantity = float(row["quantity"])
        is_buyer_maker = bool(row["is_buyer_maker"])
        normalized_rows.append({
            "ts": ts,
            "exchange": "BINANCE",
            "venue_type": venue,
            "symbol": symbol,
            "instrument_id": symbol,
            "agg_trade_id": int(row["agg_trade_id"]),
            "price": price,
            "quantity": quantity,
            "quote_quantity": price * quantity,
            "first_trade_id": int(row["first_trade_id"]),
            "last_trade_id": int(row["last_trade_id"]),
            "is_buyer_maker": is_buyer_maker,
            "side": "SELL" if is_buyer_maker else "BUY",
            "source": "binance_vision_aggTrades",
            "ingested_at": ingested_at,
        })

    df = pl.DataFrame(normalized_rows, schema={
        "ts": pl.Datetime("us"),
        "exchange": pl.Utf8,
        "venue_type": pl.Utf8,
        "symbol": pl.Utf8,
        "instrument_id": pl.Utf8,
        "agg_trade_id": pl.Int64,
        "price": pl.Float64,
        "quantity": pl.Float64,
        "quote_quantity": pl.Float64,
        "first_trade_id": pl.Int64,
        "last_trade_id": pl.Int64,
        "is_buyer_maker": pl.Boolean,
        "side": pl.Utf8,
        "source": pl.Utf8,
        "ingested_at": pl.Datetime("us"),
    })

    if not df["ts"].is_sorted():
        df = df.sort("ts")
    return df


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

    def import_aggtrades_period(
        self,
        market: Market,
        symbol: str,
        frequency: Frequency,
        start_date: str,
        end_date: str,
    ) -> "pl.DataFrame":
        """Import aggTrades for a date range into the StandardTrade schema."""
        dates = self._generate_dates(frequency, start_date, end_date)
        if not dates:
            raise ValueError(f"No dates generated for range {start_date} to {end_date}")

        all_dfs = []
        failed_dates = []
        for date in dates:
            try:
                url = build_binance_vision_aggtrades_url(market, symbol, frequency, date)
                rows = read_binance_aggtrades_zip(url, timeout=self.timeout)
                if rows:
                    df = normalize_binance_aggtrades(rows, market=market, symbol=symbol)
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

    def import_funding_period(
        self, symbol: str, start_date: str, end_date: str,
    ) -> "pl.DataFrame":
        """Import monthly USD-M funding settlements for a date range."""
        dates = self._generate_dates("monthly", start_date, end_date)
        all_dfs = []
        failed_dates = []
        for date in dates:
            try:
                rows = read_binance_funding_zip(
                    build_binance_vision_funding_url(symbol, date), timeout=self.timeout,
                )
                if rows:
                    all_dfs.append(normalize_binance_funding(rows, symbol=symbol))
            except (HTTPError, IOError, ValueError) as exc:
                failed_dates.append((date, str(exc)))
        if not all_dfs:
            raise ValueError(f"Failed to import any funding data. Failed dates: {failed_dates}")
        if failed_dates:
            for date, error in failed_dates[:3]:
                print(f"Warning: failed to import funding {date}: {error}")
        import polars as pl  # noqa: PLC0415
        return pl.concat(all_dfs).sort("ts")

    def import_funding_api_period(
        self, symbol: str, start_date: str, end_date: str,
    ) -> "pl.DataFrame":
        """Import the current unarchived funding tail from Binance REST."""
        return normalize_binance_funding(
            read_binance_funding_api(
                symbol, start_date, end_date, timeout=self.timeout,
            ),
            symbol=symbol,
        )

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
    "StandardBar",
    # aggTrades (trade data)
    "build_binance_vision_aggtrades_url",
    "read_binance_aggtrades_zip",
    "normalize_binance_aggtrades",
    "build_binance_vision_funding_url",
    "read_binance_funding_zip",
    "normalize_binance_funding",
    "read_binance_funding_api",
    "StandardTrade",
    "Market",
    "Frequency",
]
