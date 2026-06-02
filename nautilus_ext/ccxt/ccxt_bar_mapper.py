"""
Convert a ccxt OHLCV DataFrame (millisecond timestamps) into Nautilus Bar objects.

Pipeline
--------
raw OHLCV DataFrame  →  BarDataAdapter.normalize()  →  NautilusBarBuilder.build()  →  list[Bar]

The raw DataFrame produced by CcxtOhlcvConnector already has the correct
column names (open/high/low/close/volume) and a "timestamp_ms" column.
BarDataAdapter handles dedup, sort, type coercion and the UTC DatetimeIndex
that BarDataWrangler expects.
"""
from __future__ import annotations

import logging

import pandas as pd

from nautilus_ext.adapters.bar_adapter import BarDataAdapter, BarFieldMapping
from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig

# NautilusBarBuilder and BarTypeFactory are lazy-imported inside methods so that
# this module can be imported even when the nautilus_trader Cython extensions are
# not compiled (e.g. on a development machine without a full Nautilus build).

log = logging.getLogger(__name__)


class CcxtBarMapper:
    """Convert ccxt OHLCV data to Nautilus Bar objects for a single instrument."""

    def __init__(self, config: CcxtDataConfig, instrument) -> None:
        self.config = config
        self.instrument = instrument
        self._bar_type = None

    @property
    def bar_type(self):
        if self._bar_type is None:
            from nautilus_ext.builders.bar_type_factory import BarTypeFactory
            self._bar_type = BarTypeFactory.create(
                instrument=self.instrument,
                timeframe=self.config.nautilus_timeframe,
                price_type=self.config.price_type,
                source=self.config.source,
            )
        return self._bar_type

    def map(self, ohlcv_df: pd.DataFrame) -> list:
        """Convert raw OHLCV DataFrame to a list of Nautilus Bar objects.

        Parameters
        ----------
        ohlcv_df : pd.DataFrame
            Output of CcxtOhlcvConnector.fetch() — must have at minimum
            columns: timestamp_ms, open, high, low, close, volume.

        Returns
        -------
        list[Bar]
        """
        if ohlcv_df is None or ohlcv_df.empty:
            raise ValueError(
                "Cannot build Nautilus bars from an empty OHLCV DataFrame. "
                "Check that the configured date range contains data."
            )

        from nautilus_ext.builders.bar_builder import NautilusBarBuilder
        normalized_df = self._normalize(ohlcv_df)
        bars = NautilusBarBuilder(self.instrument, self.bar_type).build(normalized_df)
        log.info(
            "Built %d Nautilus bars for %r (bar_type=%r).",
            len(bars), str(self.instrument.id), str(self.bar_type),
        )
        return bars

    def normalized_df(self, ohlcv_df: pd.DataFrame) -> pd.DataFrame:
        """Return the intermediate normalized DataFrame (UTC DatetimeIndex, float OHLCV)."""
        return self._normalize(ohlcv_df)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _normalize(self, ohlcv_df: pd.DataFrame) -> pd.DataFrame:
        """Normalize the raw OHLCV DataFrame to the format expected by BarDataWrangler.

        The output has a UTC DatetimeIndex and float columns: open, high, low, close, volume.
        Millisecond timestamps in 'timestamp_ms' are converted to UTC via BarDataAdapter.
        """
        mapping = BarFieldMapping(
            timestamp="timestamp_ms",
            open="open",
            high="high",
            low="low",
            close="close",
            volume="volume",
        )
        # timestamp_unit="ms" → pd.to_datetime(ts, unit="ms", utc=True)
        adapter = BarDataAdapter(mapping, timestamp_unit="ms")
        return adapter.normalize(ohlcv_df)
