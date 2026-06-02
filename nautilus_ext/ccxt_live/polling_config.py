"""
Configuration dataclass for the ccxt REST polling / paper live runner.

Security constraints
--------------------
- enable_order_submit defaults to False and MUST remain False.
  Passing enable_order_submit=True raises NotImplementedError immediately.
- Credentials are read from environment variables when not supplied inline.
- Secrets are never logged or printed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from nautilus_ext.ccxt.ccxt_config import CCXT_TIMEFRAME_MAP, SUPPORTED_MARKET_TYPES

# Maps ccxt timeframe strings to their duration in milliseconds.
# Used by CcxtPollingBarFeed to compute look-back windows.
TF_TO_MS: dict[str, int] = {
    "1s":   1_000,
    "5s":   5_000,
    "15s":  15_000,
    "30s":  30_000,
    "1m":   60_000,
    "3m":   180_000,
    "5m":   300_000,
    "10m":  600_000,
    "15m":  900_000,
    "20m":  1_200_000,
    "30m":  1_800_000,
    "1h":   3_600_000,
    "2h":   7_200_000,
    "3h":   10_800_000,
    "4h":   14_400_000,
    "6h":   21_600_000,
    "8h":   28_800_000,
    "12h":  43_200_000,
    "1d":   86_400_000,
    "1w":   604_800_000,
    "1M":   2_592_000_000,
}


@dataclass
class CcxtPollingLiveConfig:
    """All settings for a single-symbol ccxt REST polling / paper live session.

    Parameters
    ----------
    exchange_id : str
        ccxt exchange id, e.g. "binance", "okx", "bybit".
    market_type : str
        One of "spot", "swap", "future".
    symbol : str
        Single ccxt symbol, e.g. "BTC/USDT" (spot) or "BTC/USDT:USDT" (perp).
    timeframe : str
        ccxt timeframe string, e.g. "1m", "5m", "1h".
    venue : str
        Nautilus Venue name, e.g. "BINANCE".
    poll_interval_seconds : float
        Seconds to sleep between REST polls. Default 60.
    lookback_bars : int
        Number of recent candles to fetch per poll (>= 2). Default 5.
    drop_incomplete_bar : bool
        Drop the last (still-open) bar from each fetch. Default True.
    output_dir : str | None
        Root directory for paper live output files.
    dry_run : bool
        Paper-live mode; always True. Default True.
    enable_order_submit : bool
        MUST be False. Raises NotImplementedError if True.
    max_runtime_seconds : float | None
        Stop the runner after this many seconds. None = run indefinitely.
    max_bars : int | None
        Stop after processing this many new bars. None = run indefinitely.
    since : str | None
        ISO-8601 UTC start for warmup download. If None, warmup_bars is used
        to compute the start automatically.
    warmup_bars : int
        Minimum bars to download for indicator warm-up. Default 100.
    api_key / secret / password : str | None
        Credentials. When None, read from env vars
        {EXCHANGE_ID_UPPER}_API_KEY, _SECRET, _PASSWORD.
        Public OHLCV data never requires credentials.
    params : dict | None
        Extra kwargs forwarded to the ccxt exchange constructor.
    price_type : str
        Nautilus PriceType for BarType string. Default "LAST".
    source : str
        Nautilus AggregationSource for BarType string. Default "EXTERNAL".
    instrument_kind : str | None
        Force "spot", "perpetual", or "future" instead of ccxt inference.
    trade_size : float
        Notional quantity for dry-run order intent records. Default 1.0.
    """

    exchange_id: str
    market_type: str
    symbol: str
    timeframe: str
    venue: str

    poll_interval_seconds: float = 60.0
    lookback_bars: int = 5
    drop_incomplete_bar: bool = True
    output_dir: str | None = None
    dry_run: bool = True
    enable_order_submit: bool = False
    max_runtime_seconds: float | None = None
    max_bars: int | None = None
    since: str | None = None
    warmup_bars: int = 100

    api_key: str | None = None
    secret: str | None = None
    password: str | None = None
    params: dict[str, Any] | None = None

    price_type: str = "LAST"
    source: str = "EXTERNAL"
    instrument_kind: str | None = None
    trade_size: float = 1.0

    def __post_init__(self) -> None:
        if not self.exchange_id:
            raise ValueError("exchange_id is required (e.g. 'binance', 'okx', 'bybit').")
        if not self.symbol:
            raise ValueError("symbol is required — single ccxt symbol, e.g. 'BTC/USDT'.")
        if not self.timeframe:
            raise ValueError("timeframe is required (e.g. '1m', '1h', '1d').")
        if self.timeframe not in CCXT_TIMEFRAME_MAP:
            raise ValueError(
                f"Unsupported timeframe {self.timeframe!r}. "
                f"Supported: {sorted(CCXT_TIMEFRAME_MAP)}"
            )
        if self.market_type not in SUPPORTED_MARKET_TYPES:
            raise ValueError(
                f"Unsupported market_type {self.market_type!r}. "
                f"Supported: {sorted(SUPPORTED_MARKET_TYPES)}"
            )
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0.")
        if self.lookback_bars < 2:
            raise ValueError("lookback_bars must be >= 2.")
        if self.warmup_bars < 0:
            raise ValueError("warmup_bars must be >= 0.")
        if self.enable_order_submit:
            raise NotImplementedError(
                "enable_order_submit=True is not supported. "
                "This runner is paper live (dry_run) only. "
                "Real order submission is not implemented."
            )

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def resolved_venue(self) -> str:
        return self.venue.upper() if self.venue else self.exchange_id.upper()

    @property
    def nautilus_timeframe(self) -> str:
        """Nautilus BarSpecification 'step-AGGREGATION' string."""
        return CCXT_TIMEFRAME_MAP[self.timeframe]

    @property
    def tf_ms(self) -> int:
        """Duration of one bar in milliseconds."""
        return TF_TO_MS[self.timeframe]

    def resolved_api_key(self) -> str | None:
        return self.api_key or os.environ.get(f"{self.exchange_id.upper()}_API_KEY")

    def resolved_secret(self) -> str | None:
        return self.secret or os.environ.get(f"{self.exchange_id.upper()}_SECRET")

    def resolved_password(self) -> str | None:
        return self.password or os.environ.get(f"{self.exchange_id.upper()}_PASSWORD")

    def as_ccxt_data_config(self, since: str, until: str | None = None):
        """Create a CcxtDataConfig for one-shot historical download (warmup)."""
        from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
        return CcxtDataConfig(
            exchange_id=self.exchange_id,
            market_type=self.market_type,
            symbols=[self.symbol],
            timeframe=self.timeframe,
            since=since,
            until=until,
            api_key=self.api_key,
            secret=self.secret,
            password=self.password,
            params=self.params,
            venue=self.venue,
            instrument_kind=self.instrument_kind,
            drop_incomplete_bar=self.drop_incomplete_bar,
            price_type=self.price_type,
            source=self.source,
            # Suppress file outputs for warmup
            output_dir=None,
            save_raw=False,
            save_parquet=False,
        )
