"""
CcxtPollingBarFeed — ccxt REST-based bar feed for paper live sessions.

Data flow
---------
1. initialize()   — load exchange markets, build Nautilus Instrument.
2. warmup()       — paginated historical download to pre-seed the signal engine.
3. poll_once()    — fetch the last N candles; return only new, complete bars.

Key behaviours
--------------
- Deduplication via a seen-timestamp set: each ts_event is delivered at most once.
- "Incomplete bar" detection: the last candle returned by ccxt REST is the
  currently-open bar; it is dropped when drop_incomplete_bar=True.
- Retry with exponential back-off on transient network errors (up to 3 attempts).
- All logging shows fetch time, row count, new bar count, and last bar datetime.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from nautilus_ext.ccxt_live.polling_config import CcxtPollingLiveConfig

log = logging.getLogger(__name__)

_RAW_COLS = ["timestamp_ms", "open", "high", "low", "close", "volume"]


def _ms_to_iso8601(ms: int) -> str:
    """Convert millisecond POSIX timestamp to ISO-8601 UTC string."""
    return pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_ohlcv_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "timestamp_ms", "open", "high", "low", "close", "volume",
            "datetime", "symbol", "exchange", "timeframe",
        ]
    )


class CcxtPollingBarFeed:
    """Streaming bar feed driven by ccxt REST polling.

    Usage
    -----
    feed = CcxtPollingBarFeed(config)
    feed.initialize()          # loads markets, builds Nautilus Instrument
    warmup_df = feed.warmup()  # historical bars for signal engine pre-heating
    new_df = feed.poll_once()  # call repeatedly in a loop

    Parameters
    ----------
    config : CcxtPollingLiveConfig
        Full live session configuration.
    """

    def __init__(self, config: CcxtPollingLiveConfig) -> None:
        self.config = config
        self._exchange = None
        self._instrument = None
        self._market = None
        self._market_type: str | None = None
        self._seen_ts: set[int] = set()
        self._last_seen_ts: int | None = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Load exchange markets and build the Nautilus Instrument.

        Must be called before warmup() or poll_once().
        Requires nautilus_trader Cython extensions.
        """
        from nautilus_ext.ccxt.ccxt_market_connector import CcxtMarketConnector
        from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper

        data_cfg = self.config.as_ccxt_data_config(
            since=_ms_to_iso8601(int(time.time() * 1000) - self.config.tf_ms * 2),
        )
        market_connector = CcxtMarketConnector(data_cfg)
        self._exchange = market_connector.exchange

        markets = market_connector.load_markets()
        sym = self.config.symbol
        if sym not in markets:
            sample = sorted(markets.keys())[:10]
            raise ValueError(
                f"Symbol {sym!r} not found in {self.config.exchange_id!r} markets. "
                f"First 10 available: {sample}"
            )

        self._market = markets[sym]
        self._market_type = market_connector.infer_market_type(self._market)

        mapper = CcxtInstrumentMapper(data_cfg, precision_mode=market_connector.precision_mode)
        self._instrument = mapper.build_instrument(self._market, self._market_type)
        self._initialized = True
        log.info(
            "Feed initialized: symbol=%r  instrument_id=%r  market_type=%r",
            sym, str(self._instrument.id), self._market_type,
        )

    def warmup(self) -> pd.DataFrame:
        """Download historical bars to pre-warm the signal engine.

        Uses config.since if set, otherwise computes start from warmup_bars.
        Returns a DataFrame with full OHLCV columns sorted by timestamp_ms.
        The returned bars are registered in the seen-timestamp set so they
        are NOT re-delivered by subsequent poll_once() calls.
        """
        if not self._initialized:
            raise RuntimeError("Call initialize() before warmup().")

        now_ms = int(time.time() * 1000)
        if self.config.since:
            since_ms = int(pd.Timestamp(self.config.since).timestamp() * 1000)
        else:
            since_ms = now_ms - self.config.warmup_bars * self.config.tf_ms

        since_str = _ms_to_iso8601(since_ms)
        log.info(
            "Warmup download: symbol=%r  since=%s  (warmup_bars=%d)",
            self.config.symbol, since_str, self.config.warmup_bars,
        )

        data_cfg = self.config.as_ccxt_data_config(since=since_str, until=None)
        from nautilus_ext.ccxt.ccxt_ohlcv_connector import CcxtOhlcvConnector
        connector = CcxtOhlcvConnector(data_cfg, self._exchange)
        df = connector.fetch(self.config.symbol)

        if not df.empty:
            for ts in df["timestamp_ms"]:
                self._seen_ts.add(int(ts))
            self._last_seen_ts = int(df["timestamp_ms"].iloc[-1])

        log.info(
            "Warmup complete: %d bars  last=%s",
            len(df),
            df["datetime"].iloc[-1] if not df.empty else None,
        )
        return df

    def poll_once(self) -> pd.DataFrame:
        """Fetch recent candles and return only new complete bars.

        New = timestamp_ms not previously seen.
        Complete = not the last bar in the response when drop_incomplete_bar=True.

        Returns an empty DataFrame when there are no new bars.
        """
        fetch_start = time.time()

        if self._last_seen_ts is not None:
            since_ms = self._last_seen_ts + 1
        else:
            since_ms = int(time.time() * 1000) - self.config.lookback_bars * self.config.tf_ms

        # Fetch a few extra rows so we can safely drop the incomplete one.
        limit = self.config.lookback_bars + 2

        rows = self._fetch_with_retry(self.config.symbol, since_ms, limit)

        if not rows:
            log.debug("poll_once: no rows returned.")
            return _empty_ohlcv_df()

        df = self._rows_to_df(rows)

        # Keep only unseen timestamps
        df = df[~df["timestamp_ms"].isin(self._seen_ts)].copy()
        df = df.sort_values("timestamp_ms").reset_index(drop=True)

        # Drop the last (still-open) bar
        if self.config.drop_incomplete_bar and len(df) > 0:
            df = df.iloc[:-1].copy()

        if not df.empty:
            for ts in df["timestamp_ms"]:
                self._seen_ts.add(int(ts))
            self._last_seen_ts = int(df["timestamp_ms"].iloc[-1])

        elapsed = time.time() - fetch_start
        log.info(
            "poll_once: fetched %d rows → %d new bars  last=%s  (%.2fs)",
            len(rows), len(df),
            df["datetime"].iloc[-1] if not df.empty else None,
            elapsed,
        )
        return df

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def instrument(self):
        """Nautilus Instrument (available after initialize())."""
        if self._instrument is None:
            raise RuntimeError("Call initialize() first.")
        return self._instrument

    @property
    def market_type(self) -> str:
        if self._market_type is None:
            raise RuntimeError("Call initialize() first.")
        return self._market_type

    @property
    def bar_type_str(self) -> str:
        """Nautilus BarType string for the configured symbol/timeframe."""
        instrument_id = str(self.instrument.id)
        tf = self.config.nautilus_timeframe
        return f"{instrument_id}-{tf}-{self.config.price_type}-{self.config.source}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_with_retry(
        self, symbol: str, since_ms: int, limit: int, max_retries: int = 3
    ) -> list[list]:
        backoff = 2.0
        for attempt in range(max_retries):
            try:
                rows = self._exchange.fetch_ohlcv(
                    symbol,
                    self.config.timeframe,
                    since=since_ms,
                    limit=limit,
                )
                return rows or []
            except Exception as exc:
                if attempt < max_retries - 1:
                    log.warning(
                        "fetch_ohlcv failed (attempt %d/%d): %s  retrying in %.1fs …",
                        attempt + 1, max_retries, exc, backoff,
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                else:
                    log.error("fetch_ohlcv failed after %d attempts: %s", max_retries, exc)
                    raise

    def _rows_to_df(self, rows: list[list]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=_RAW_COLS)
        df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        df["symbol"] = self.config.symbol
        df["exchange"] = self.config.exchange_id
        df["timeframe"] = self.config.timeframe
        return df
