from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from nautilus_ext.data.events import QuoteTickEvent


_REQUIRED_COLUMNS = {
    "bid_price",
    "ask_price",
    "bid_size",
    "ask_size",
    "ts_event",
    "ts_init",
}


class CatalogQuoteTickSource:
    """Read Nautilus QuoteTick parquet data without modifying the catalog."""

    def __init__(
        self,
        catalog_path: str,
        instrument_id: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        price_scale: float = 1_000_000_000,
        size_scale: float = 1_000_000_000,
    ) -> None:
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1 when provided.")
        self.catalog_path = Path(catalog_path)
        self.instrument_id = instrument_id
        self.start = start
        self.end = end
        self.limit = limit
        self.price_scale = price_scale
        self.size_scale = size_scale
        self.files: list[Path] = []
        self.emitted_events = 0

    def iter_events(self) -> Iterator[QuoteTickEvent]:
        frame = self._load_frame()
        self.emitted_events = 0
        for row in frame.itertuples(index=False):
            self.emitted_events += 1
            yield QuoteTickEvent(
                instrument_id=self.instrument_id,
                bid_price=row.bid_price,
                ask_price=row.ask_price,
                bid_size=row.bid_size,
                ask_size=row.ask_size,
                ts_event=row.ts_event.to_pydatetime(),
                ts_init=None if pd.isna(row.ts_init) else row.ts_init.to_pydatetime(),
                source="nautilus_catalog_quote_tick",
            )

    def _load_frame(self) -> pd.DataFrame:
        self.files = self._find_files()
        frames: list[pd.DataFrame] = []
        for path in self.files:
            frame = pd.read_parquet(path)
            missing = sorted(_REQUIRED_COLUMNS.difference(frame.columns))
            if missing:
                raise ValueError(
                    f"QuoteTick parquet file {path} is missing columns {missing}; "
                    f"actual columns: {list(frame.columns)}."
                )
            frames.append(frame[list(_REQUIRED_COLUMNS)])

        df = pd.concat(frames, ignore_index=True)
        df["ts_event"] = _to_timestamp(df["ts_event"])
        df["ts_init"] = _to_timestamp(df["ts_init"])
        df["bid_price"] = _decode_column(df["bid_price"], self.price_scale)
        df["ask_price"] = _decode_column(df["ask_price"], self.price_scale)
        df["bid_size"] = _decode_column(df["bid_size"], self.size_scale)
        df["ask_size"] = _decode_column(df["ask_size"], self.size_scale)
        df = df[
            df["ts_event"].notna()
            & (df["bid_price"] > 0)
            & (df["ask_price"] > 0)
            & (df["ask_price"] >= df["bid_price"])
        ].sort_values("ts_event")
        if self.start is not None:
            df = df[df["ts_event"] >= pd.to_datetime(self.start, utc=True)]
        if self.end is not None:
            df = df[df["ts_event"] <= pd.to_datetime(self.end, utc=True)]
        if self.limit is not None:
            df = df.head(self.limit)
        if df.empty:
            raise ValueError(
                f"No valid QuoteTick events for instrument_id={self.instrument_id!r} "
                f"after parsing/filtering files under {self.catalog_path}."
            )
        return df

    def _find_files(self) -> list[Path]:
        paths = sorted(
            path
            for path in self.catalog_path.rglob("*.parquet")
            if "quote_tick" in str(path).lower() and self.instrument_id.lower() in str(path).lower()
        )
        if not paths:
            raise ValueError(
                "No QuoteTick parquet files found: "
                f"catalog_path={self.catalog_path}, instrument_id={self.instrument_id!r}, "
                "search_keyword='quote_tick'."
            )
        return paths


def _decode_column(series: pd.Series, scale: float) -> pd.Series:
    return series.map(lambda value: _decode_value(value, scale))


def _decode_value(value, scale: float) -> float:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return int.from_bytes(value, byteorder="little", signed=True) / scale
    return float(pd.to_numeric(value, errors="coerce"))


def _to_timestamp(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_datetime(series, unit="ns", utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")
