"""
CcxtBarDataConnector — high-level facade that ties all ccxt sub-modules together.

Interface contract (compatible with NautilusBacktestRunner)
-----------------------------------------------------------
    connector.prepare_data()   → list[Bar]
    connector.get_bar_type()   → BarType
    connector.instrument       → Nautilus Instrument   (attribute)

Typical usage
-------------
    from nautilus_ext.ccxt import CcxtBarDataConnector, CcxtDataConfig

    config = CcxtDataConfig(
        exchange_id="binance",
        market_type="swap",
        symbols=["BTC/USDT"],
        timeframe="1m",
        since="2024-01-01T00:00:00Z",
        until="2024-01-07T00:00:00Z",
        venue="BINANCE",
        output_dir="outputs/ccxt/BTCUSDT_1m",
    )
    connector = CcxtBarDataConnector(config)
    bars = connector.prepare_data()        # list[Bar]
    bar_type = connector.get_bar_type()    # BarType
    instrument = connector.instrument      # CryptoPerpetual

Multi-symbol note
-----------------
If config.symbols contains more than one symbol, prepare_data() returns
bars for the FIRST symbol only so it remains compatible with
NautilusBacktestRunner.  For downloading multiple symbols independently,
call download_ohlcv(symbol) and build_instrument(symbol) per symbol and
manage the results yourself.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from nautilus_ext.ccxt.ccxt_bar_mapper import CcxtBarMapper
from nautilus_ext.ccxt.ccxt_cache import CcxtCache
from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig
from nautilus_ext.ccxt.ccxt_instrument_mapper import CcxtInstrumentMapper
from nautilus_ext.ccxt.ccxt_market_connector import CcxtMarketConnector
from nautilus_ext.ccxt.ccxt_ohlcv_connector import CcxtOhlcvConnector

log = logging.getLogger(__name__)


class CcxtBarDataConnector:
    """Unified ccxt → Nautilus bar data connector.

    Parameters
    ----------
    config : CcxtDataConfig
        All settings (exchange, symbols, timeframe, date range, outputs, …).
    """

    def __init__(self, config: CcxtDataConfig) -> None:
        self.config = config

        self._market_connector = CcxtMarketConnector(config)

        # Per-symbol state — populated on first call to prepare_data()
        self._markets: dict[str, dict] = {}          # symbol → raw ccxt market
        self._market_types: dict[str, str] = {}      # symbol → "spot" / "swap_linear" / …
        self._instruments: dict[str, object] = {}    # symbol → Nautilus Instrument
        self._ohlcv_dfs: dict[str, pd.DataFrame] = {}  # symbol → raw OHLCV DataFrame
        self._bar_types: dict[str, object] = {}      # symbol → BarType
        self._bars: dict[str, list] = {}             # symbol → list[Bar]

        self._prepared = False

    # ------------------------------------------------------------------
    # BacktestRunner-compatible interface
    # ------------------------------------------------------------------

    @property
    def instrument(self):
        """Return the Nautilus Instrument for the first configured symbol.

        Call prepare_data() first to populate this.
        """
        if not self._instruments:
            self.prepare_data()
        return self._instruments[self._primary_symbol]

    def prepare_data(self) -> list:
        """Download, build instrument, and build bars for the first symbol.

        Subsequent calls return the cached result immediately.

        Returns
        -------
        list[Bar]
        """
        if self._prepared:
            return self._bars.get(self._primary_symbol, [])

        symbol = self._primary_symbol
        if len(self.config.symbols) > 1:
            log.warning(
                "CcxtBarDataConnector.prepare_data() returns bars for the first symbol "
                "only (%r).  For multi-symbol use, call download_ohlcv(symbol) per symbol.",
                symbol,
            )

        self.load_markets()
        self.build_instrument(symbol)
        self.download_ohlcv(symbol)
        self._build_bars(symbol)

        self._prepared = True
        return self._bars[symbol]

    def get_bars(self) -> list:
        return self.prepare_data()

    def get_bar_type(self):
        if self._primary_symbol not in self._bar_types:
            self.prepare_data()
        return self._bar_types[self._primary_symbol]

    def get_instrument(self):
        return self.instrument

    # ------------------------------------------------------------------
    # Step-by-step API
    # ------------------------------------------------------------------

    def load_markets(self) -> dict:
        """Download and cache all markets from the exchange.

        Returns the raw ccxt markets dict (keyed by ccxt symbol string).
        """
        all_markets = self._market_connector.load_markets()

        for symbol in self.config.symbols:
            if symbol not in all_markets:
                log.warning(
                    "Symbol %r not found in %r markets; it will be skipped.",
                    symbol, self.config.exchange_id,
                )
                continue
            market = all_markets[symbol]
            self._markets[symbol] = market
            self._market_types[symbol] = self._market_connector.infer_market_type(market)

        return all_markets

    def build_instrument(self, symbol: str):
        """Build and cache the Nautilus Instrument for a single symbol.

        Parameters
        ----------
        symbol : str
            ccxt symbol string, e.g. "BTC/USDT" or "BTC/USDT:USDT".
        """
        if symbol not in self._markets:
            self.load_markets()
        if symbol not in self._markets:
            raise ValueError(
                f"Symbol {symbol!r} not found in {self.config.exchange_id!r} markets. "
                f"Check config.symbols."
            )

        market = self._markets[symbol]
        market_type = self._market_types[symbol]
        mapper = CcxtInstrumentMapper(
            self.config, precision_mode=self._market_connector.precision_mode
        )
        instrument = mapper.build_instrument(market, market_type)
        self._instruments[symbol] = instrument
        log.info(
            "Built Nautilus Instrument: %r  (type=%r, id=%r)",
            symbol, market_type, str(instrument.id),
        )
        return instrument

    def download_ohlcv(self, symbol: str) -> pd.DataFrame:
        """Download and cache OHLCV data for a single symbol.

        Returns the raw OHLCV DataFrame with columns:
            timestamp_ms, open, high, low, close, volume, datetime, …
        """
        if symbol not in self._instruments:
            self.build_instrument(symbol)

        ohlcv_connector = CcxtOhlcvConnector(
            self.config, self._market_connector.exchange
        )
        df = ohlcv_connector.fetch(symbol)
        if df.empty:
            raise ValueError(
                f"No OHLCV data returned for symbol={symbol!r} "
                f"(exchange={self.config.exchange_id!r}, "
                f"timeframe={self.config.timeframe!r}, "
                f"since={self.config.since!r}, until={self.config.until!r})."
            )
        self._ohlcv_dfs[symbol] = df
        return df

    def get_raw_df(self, symbol: str | None = None) -> pd.DataFrame:
        sym = symbol or self._primary_symbol
        if sym not in self._ohlcv_dfs:
            self.download_ohlcv(sym)
        return self._ohlcv_dfs[sym]

    def get_normalized_df(self, symbol: str | None = None) -> pd.DataFrame:
        """Return the UTC-indexed, float-typed OHLCV DataFrame after BarDataAdapter normalisation."""
        sym = symbol or self._primary_symbol
        if sym not in self._instruments:
            self.build_instrument(sym)
        if sym not in self._ohlcv_dfs:
            self.download_ohlcv(sym)
        mapper = CcxtBarMapper(self.config, self._instruments[sym])
        return mapper.normalized_df(self._ohlcv_dfs[sym])

    # ------------------------------------------------------------------
    # Market discovery
    # ------------------------------------------------------------------

    def discover(self) -> dict:
        """Return a summary of the configured symbols and their market types."""
        self.load_markets()
        return {
            sym: {
                "market_type": self._market_types.get(sym, "unknown"),
                "base": self._markets.get(sym, {}).get("base"),
                "quote": self._markets.get(sym, {}).get("quote"),
                "settle": self._markets.get(sym, {}).get("settle"),
                "active": self._markets.get(sym, {}).get("active"),
            }
            for sym in self.config.symbols
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_outputs(self) -> dict[str, Path]:
        """Save all available artefacts to config.output_dir.

        Returns a dict mapping artefact name → saved Path.

        Artefacts
        ---------
        markets.json            raw ccxt market metadata
        raw_ohlcv.csv           raw OHLCV data (per symbol)
        raw_ohlcv.parquet       same, Parquet format
        normalized_bars.parquet UTC-indexed OHLCV after normalisation
        connector_profile.json  config summary + instrument / bar_type strings
        """
        if not self.config.output_dir:
            raise ValueError(
                "config.output_dir must be set to save outputs. "
                "Example: config.output_dir='outputs/ccxt/BTCUSDT_1m'"
            )

        if not self._prepared:
            self.prepare_data()

        cache = CcxtCache(self.config.output_dir)
        saved: dict[str, Path] = {}
        xid = self.config.exchange_id

        # markets.json — exchange-level
        all_markets = self._market_connector.load_markets()
        saved["markets.json"] = cache.save_markets_json(all_markets, xid)

        for symbol in self.config.symbols:
            if symbol not in self._ohlcv_dfs:
                continue
            df = self._ohlcv_dfs[symbol]
            tf = self.config.timeframe

            if self.config.save_raw:
                saved[f"{symbol}/raw_ohlcv.csv"] = cache.save_raw_csv(df, xid, symbol, tf)

            if self.config.save_parquet:
                saved[f"{symbol}/raw_ohlcv.parquet"] = cache.save_raw_parquet(df, xid, symbol, tf)
                if symbol in self._instruments:
                    try:
                        norm_df = self.get_normalized_df(symbol)
                        saved[f"{symbol}/normalized_bars.parquet"] = cache.save_normalized_parquet(
                            norm_df, xid, symbol, tf
                        )
                    except Exception as exc:
                        log.warning("Could not save normalized_bars.parquet for %r: %s", symbol, exc)

            # connector_profile.json
            profile_data: dict = {
                "exchange_id": self.config.exchange_id,
                "symbol": symbol,
                "market_type": self._market_types.get(symbol),
                "timeframe": self.config.timeframe,
                "nautilus_timeframe": self.config.nautilus_timeframe,
                "since": self.config.since,
                "until": self.config.until,
                "venue": self.config.resolved_venue,
                "bars_count": len(self._bars.get(symbol, [])),
            }
            if symbol in self._instruments:
                profile_data["instrument_id"] = str(self._instruments[symbol].id)
            if symbol in self._bar_types:
                profile_data["bar_type"] = str(self._bar_types[symbol])

            saved[f"{symbol}/connector_profile.json"] = cache.save_connector_profile(
                profile_data, xid, symbol, tf
            )

        return saved

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_bars(self, symbol: str) -> list:
        instrument = self._instruments[symbol]
        ohlcv_df = self._ohlcv_dfs[symbol]
        mapper = CcxtBarMapper(self.config, instrument)
        bars = mapper.map(ohlcv_df)
        self._bar_types[symbol] = mapper.bar_type
        self._bars[symbol] = bars
        return bars

    @property
    def _primary_symbol(self) -> str:
        return self.config.symbols[0]
