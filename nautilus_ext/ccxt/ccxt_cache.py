"""
Optional file-system cache for ccxt-derived artefacts.

Saved outputs
-------------
markets.json          — raw ccxt market metadata dict
raw_ohlcv.csv         — raw OHLCV with timestamp_ms column
raw_ohlcv.parquet     — same, in Parquet format
normalized_bars.parquet — UTC-indexed OHLCV after BarDataAdapter normalisation
connector_profile.json  — config summary + instrument_id + bar_type string

All paths are derived from config.output_dir / exchange_id / symbol / timeframe.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


class CcxtCache:
    """Write artefacts produced by the ccxt connector to disk."""

    def __init__(self, output_dir: str | Path) -> None:
        self.root = Path(output_dir)

    def symbol_dir(self, exchange_id: str, symbol: str, timeframe: str) -> Path:
        safe_sym = symbol.replace("/", "_").replace(":", "_")
        return self.root / exchange_id / safe_sym / timeframe

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def save_markets_json(
        self, markets: dict, exchange_id: str, path: str | Path | None = None
    ) -> Path:
        dest = Path(path) if path else (self.root / exchange_id / "markets.json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            json.dump(markets, fh, indent=2, default=str)
        log.info("Saved markets.json (%d symbols) → %s", len(markets), dest)
        return dest

    def save_raw_csv(
        self,
        df: pd.DataFrame,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        path: str | Path | None = None,
    ) -> Path:
        dest = Path(path) if path else (self.symbol_dir(exchange_id, symbol, timeframe) / "raw_ohlcv.csv")
        dest.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(dest, index=False)
        log.info("Saved raw OHLCV CSV (%d rows) → %s", len(df), dest)
        return dest

    def save_raw_parquet(
        self,
        df: pd.DataFrame,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        path: str | Path | None = None,
    ) -> Path:
        dest = (
            Path(path)
            if path
            else (self.symbol_dir(exchange_id, symbol, timeframe) / "raw_ohlcv.parquet")
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(dest, index=False, engine="pyarrow")
        log.info("Saved raw OHLCV Parquet (%d rows) → %s", len(df), dest)
        return dest

    def save_normalized_parquet(
        self,
        normalized_df: pd.DataFrame,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        path: str | Path | None = None,
    ) -> Path:
        dest = (
            Path(path)
            if path
            else (self.symbol_dir(exchange_id, symbol, timeframe) / "normalized_bars.parquet")
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        normalized_df.to_parquet(dest, engine="pyarrow")
        log.info("Saved normalized bars Parquet (%d rows) → %s", len(normalized_df), dest)
        return dest

    def save_connector_profile(
        self,
        profile: dict,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        path: str | Path | None = None,
    ) -> Path:
        dest = (
            Path(path)
            if path
            else (self.symbol_dir(exchange_id, symbol, timeframe) / "connector_profile.json")
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2, default=str)
        log.info("Saved connector_profile.json → %s", dest)
        return dest
