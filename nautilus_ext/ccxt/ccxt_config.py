"""
Configuration dataclass for the ccxt-based bar data connector.

All credential fields default to None; if unset they are read from
environment variables  {EXCHANGE_ID_UPPER}_API_KEY / _SECRET / _PASSWORD.
Public market data (OHLCV + markets) never requires credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# Supported ccxt market type strings
SUPPORTED_MARKET_TYPES = {"spot", "swap", "future", "option"}

# ccxt timeframe → Nautilus BarSpecification "step-AGGREGATION"
CCXT_TIMEFRAME_MAP: dict[str, str] = {
    "1s":  "1-SECOND",
    "5s":  "5-SECOND",
    "15s": "15-SECOND",
    "30s": "30-SECOND",
    "1m":  "1-MINUTE",
    "3m":  "3-MINUTE",
    "5m":  "5-MINUTE",
    "10m": "10-MINUTE",
    "15m": "15-MINUTE",
    "20m": "20-MINUTE",
    "30m": "30-MINUTE",
    "1h":  "1-HOUR",
    "2h":  "2-HOUR",
    "3h":  "3-HOUR",
    "4h":  "4-HOUR",
    "6h":  "6-HOUR",
    "8h":  "8-HOUR",
    "12h": "12-HOUR",
    "1d":  "1-DAY",
    "1w":  "1-WEEK",
    "1M":  "1-MONTH",
}


@dataclass
class CcxtDataConfig:
    """Full configuration for CcxtBarDataConnector.

    Parameters
    ----------
    exchange_id : str
        ccxt exchange id, e.g. "binance", "okx", "bybit".
    market_type : str
        One of "spot", "swap", "future".  Used for filtering when listing
        markets and for default instrument_kind inference.
    symbols : list[str]
        ccxt-style symbols, e.g. ["BTC/USDT"] for spot or
        ["BTC/USDT:USDT"] for OKX perpetual.
    timeframe : str
        ccxt timeframe string, e.g. "1m", "5m", "1h", "1d".
    since : str
        ISO-8601 UTC start datetime, e.g. "2024-01-01T00:00:00Z".
    until : str | None
        ISO-8601 UTC end datetime (inclusive).  If None, stops at current bar.
    limit : int
        Maximum candles per ccxt API call (default 1000).
    enable_rate_limit : bool
        Pass True (default) to let ccxt throttle requests automatically.
    sandbox : bool
        Enable exchange sandbox/testnet mode (default False).
    api_key / secret / password : str | None
        Credentials.  When None, read from env vars
        {EXCHANGE_ID_UPPER}_API_KEY, _SECRET, _PASSWORD.
        Public data never needs credentials.
    params : dict | None
        Extra kwargs forwarded to the ccxt exchange constructor.
    output_dir : str | None
        Root directory for all saved outputs (markets.json, raw CSVs, etc.).
    save_raw : bool
        Save raw OHLCV CSV when True (default).
    save_parquet : bool
        Save raw OHLCV Parquet when True (default).
    venue : str
        Nautilus Venue name.  Defaults to exchange_id.upper().
    quote_currency : str | None
        Override quote currency (e.g. "USDT").
    base_currency : str | None
        Override base currency (e.g. "BTC").
    instrument_kind : str | None
        Force instrument type: "spot", "perpetual", or "future".
        When None, inferred from ccxt market metadata.
    drop_incomplete_bar : bool
        Drop the last (possibly still-open) bar when True (default).
    price_type : str
        Nautilus PriceType string for BarType, default "LAST".
    source : str
        Nautilus AggregationSource string for BarType, default "EXTERNAL".
    """

    exchange_id: str
    market_type: str
    symbols: list[str]
    timeframe: str
    since: str

    until: str | None = None
    limit: int = 1000
    enable_rate_limit: bool = True
    sandbox: bool = False
    api_key: str | None = None
    secret: str | None = None
    password: str | None = None
    params: dict[str, Any] | None = None

    output_dir: str | None = None
    save_raw: bool = True
    save_parquet: bool = True

    venue: str = ""
    quote_currency: str | None = None
    base_currency: str | None = None
    instrument_kind: str | None = None

    drop_incomplete_bar: bool = True
    price_type: str = "LAST"
    source: str = "EXTERNAL"

    def __post_init__(self) -> None:
        if not self.exchange_id:
            raise ValueError("exchange_id is required (e.g. 'binance', 'okx', 'bybit').")
        if not self.symbols:
            raise ValueError("symbols must be a non-empty list (e.g. ['BTC/USDT']).")
        if not self.timeframe:
            raise ValueError("timeframe is required (e.g. '1m', '1h', '1d').")
        if self.timeframe not in CCXT_TIMEFRAME_MAP:
            supported = sorted(CCXT_TIMEFRAME_MAP)
            raise ValueError(
                f"Unsupported timeframe {self.timeframe!r}. "
                f"Supported: {supported}"
            )
        if not self.since:
            raise ValueError("since is required (ISO-8601 UTC string, e.g. '2024-01-01T00:00:00Z').")
        if self.market_type and self.market_type not in SUPPORTED_MARKET_TYPES:
            raise ValueError(
                f"Unsupported market_type {self.market_type!r}. "
                f"Supported: {sorted(SUPPORTED_MARKET_TYPES)}"
            )

    @property
    def resolved_venue(self) -> str:
        return self.venue.upper() if self.venue else self.exchange_id.upper()

    @property
    def nautilus_timeframe(self) -> str:
        """Return the Nautilus BarSpecification 'step-AGGREGATION' string."""
        return CCXT_TIMEFRAME_MAP[self.timeframe]

    def resolved_api_key(self) -> str | None:
        return self.api_key or os.environ.get(f"{self.exchange_id.upper()}_API_KEY")

    def resolved_secret(self) -> str | None:
        return self.secret or os.environ.get(f"{self.exchange_id.upper()}_SECRET")

    def resolved_password(self) -> str | None:
        return self.password or os.environ.get(f"{self.exchange_id.upper()}_PASSWORD")
