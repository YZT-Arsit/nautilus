"""
Download OHLCV data from a ccxt exchange with automatic pagination.

Key behaviours
--------------
- Paginates forward from `since` until `until` (or the current bar).
- De-duplicates rows by timestamp (keeps last).
- Sorts ascending by timestamp.
- Converts millisecond timestamps to UTC datetime column.
- Optionally drops the last (incomplete) bar.
- Optionally saves raw CSV / Parquet.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig

log = logging.getLogger(__name__)

# Raw OHLCV column names from ccxt.fetch_ohlcv()
_RAW_COLS = ["timestamp_ms", "open", "high", "low", "close", "volume"]


class CcxtOhlcvConnector:
    """Download OHLCV candles for a single symbol."""

    def __init__(self, config: CcxtDataConfig, exchange) -> None:
        self.config = config
        self.exchange = exchange

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch(self, symbol: str) -> pd.DataFrame:
        """Download all candles for *symbol* in the configured timeframe / date range.

        Returns a DataFrame with columns:
            timestamp_ms, open, high, low, close, volume,
            datetime (UTC), symbol, exchange, timeframe

        Rows are sorted ascending by timestamp_ms and de-duplicated.
        """
        since_ms = self._to_ms(self.config.since)
        until_ms = self._to_ms(self.config.until) if self.config.until else None

        log.info(
            "Fetching OHLCV for %r on %r (timeframe=%r, since=%r, until=%r) ...",
            symbol, self.config.exchange_id, self.config.timeframe,
            self.config.since, self.config.until,
        )

        raw_rows = self._paginate(symbol, since_ms, until_ms)
        if not raw_rows:
            log.warning("No OHLCV data returned for %r on %r.", symbol, self.config.exchange_id)
            return self._empty_df(symbol)

        df = self._to_dataframe(raw_rows, symbol)

        # Drop the last bar if it may be incomplete (still-open candle).
        if self.config.drop_incomplete_bar and len(df) > 0:
            df = df.iloc[:-1].copy()

        if df.empty:
            log.warning(
                "DataFrame is empty after dropping incomplete bar for %r on %r.",
                symbol, self.config.exchange_id,
            )

        log.info(
            "Fetched %d bars for %r (%r → %r).",
            len(df), symbol,
            df["datetime"].iloc[0] if not df.empty else None,
            df["datetime"].iloc[-1] if not df.empty else None,
        )
        return df

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def _paginate(self, symbol: str, since_ms: int, until_ms: int | None) -> list[list]:
        all_rows: list[list] = []
        cursor = since_ms

        while True:
            batch = self._fetch_batch(symbol, cursor)
            if not batch:
                break

            # Filter out rows beyond until_ms
            if until_ms is not None:
                batch = [row for row in batch if row[0] <= until_ms]

            all_rows.extend(batch)

            # If the batch was cut short by until_ms, stop
            if until_ms is not None and (not batch or batch[-1][0] >= until_ms):
                break

            # Advance cursor past the last received timestamp
            last_ts = batch[-1][0]
            if last_ts <= cursor:
                # No progress — prevent infinite loop
                break
            cursor = last_ts + 1

            # If fewer rows than limit were returned, we reached the end
            if len(batch) < self.config.limit:
                break

        return all_rows

    def _fetch_batch(self, symbol: str, since_ms: int) -> list[list]:
        """Single ccxt fetch_ohlcv call with basic error handling."""
        try:
            rows = self.exchange.fetch_ohlcv(
                symbol,
                self.config.timeframe,
                since=since_ms,
                limit=self.config.limit,
            )
            return rows or []
        except Exception as exc:
            log.error(
                "fetch_ohlcv failed for %r at since_ms=%d: %s",
                symbol, since_ms, exc,
            )
            raise

    # ------------------------------------------------------------------
    # DataFrame construction
    # ------------------------------------------------------------------

    def _to_dataframe(self, rows: list[list], symbol: str) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=_RAW_COLS)

        # Ensure correct dtypes
        df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # UTC datetime column
        df["datetime"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)

        # Metadata columns
        df["symbol"] = symbol
        df["exchange"] = self.config.exchange_id
        df["timeframe"] = self.config.timeframe

        # De-duplicate by timestamp (keep last occurrence)
        before = len(df)
        df = df.drop_duplicates(subset="timestamp_ms", keep="last")
        if len(df) < before:
            log.debug("Dropped %d duplicate rows for %r.", before - len(df), symbol)

        # Sort ascending
        df = df.sort_values("timestamp_ms").reset_index(drop=True)

        return df

    @staticmethod
    def _empty_df(symbol: str) -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "timestamp_ms", "open", "high", "low", "close", "volume",
                "datetime", "symbol", "exchange", "timeframe",
            ]
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_raw_csv(self, df: pd.DataFrame, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(dest, index=False)
        log.info("Saved raw OHLCV CSV (%d rows) → %s", len(df), dest)
        return dest

    def save_raw_parquet(self, df: pd.DataFrame, path: str | Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(dest, index=False, engine="pyarrow")
        log.info("Saved raw OHLCV Parquet (%d rows) → %s", len(df), dest)
        return dest

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_ms(value: str | None) -> int | None:
        if value is None:
            return None
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return int(ts.timestamp() * 1000)
