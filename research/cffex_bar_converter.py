"""CFFEX quote/depth mid-price bar conversion helpers.

These helpers produce *derived* mid-price bars from Nautilus catalog quote/depth
data. They do not create trade OHLCV bars: ``volume`` is a synthetic update
count, ``quote_volume`` is zero, and ``bar_source`` records the derivation.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


BAR_COLUMNS = (
    "ts",
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "source",
    "bar_source",
    "ingested_at",
)


@dataclass(frozen=True)
class MidBar:
    ts: datetime
    instrument_id: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int
    source: str
    bar_source: str
    ingested_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "instrument_id": self.instrument_id,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "trade_count": self.trade_count,
            "source": self.source,
            "bar_source": self.bar_source,
            "ingested_at": self.ingested_at,
        }


def _to_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value) / 1_000_000_000, tz=timezone.utc)
    else:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _decode_number(value: Any, *, scale: float = 1.0) -> float:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return int.from_bytes(value, byteorder="little", signed=True) / scale
    return float(value)


def _minute_floor(ts: datetime, bar_type: str) -> datetime:
    if bar_type not in {"1m", "5m"}:
        raise ValueError(f"unsupported bar_type {bar_type!r}; expected '1m' or '5m'")
    minute = ts.minute if bar_type == "1m" else ts.minute - (ts.minute % 5)
    return ts.replace(minute=minute, second=0, microsecond=0)


def _valid_mid(bid: float, ask: float) -> bool:
    return bid > 0 and ask > 0 and ask >= bid


def quote_rows_to_mid_bars(
    rows: Iterable[dict[str, Any]],
    *,
    instrument_id: str,
    bar_type: str = "1m",
    volume_policy: str = "tick_count",
    price_scale: float = 1.0,
    ingested_at: datetime | None = None,
) -> list[MidBar]:
    """Aggregate quote rows to mid-price bars using minute-start timestamps."""
    if volume_policy not in {"tick_count", "zero"}:
        raise ValueError("volume_policy must be 'tick_count' or 'zero'")
    ingested = (ingested_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for row in rows:
        ts = _to_utc_datetime(row.get("ts_event") or row.get("ts") or row.get("event_time_ns"))
        bid = _decode_number(row["bid_price"], scale=price_scale)
        ask = _decode_number(row["ask_price"], scale=price_scale)
        if not _valid_mid(bid, ask):
            continue
        buckets[_minute_floor(ts, bar_type)].append((bid + ask) / 2.0)
    return _bars_from_buckets(
        buckets,
        instrument_id=instrument_id,
        source="cffex_quote_mid_bar",
        bar_source="quote_mid",
        volume_policy=volume_policy,
        ingested_at=ingested,
    )


def depth_rows_to_mid_bars(
    rows: Iterable[dict[str, Any]],
    *,
    instrument_id: str,
    bar_type: str = "1m",
    volume_policy: str = "tick_count",
    price_scale: float = 1.0,
    ingested_at: datetime | None = None,
) -> list[MidBar]:
    """Aggregate depth rows from top-of-book bid/ask columns to mid-price bars."""
    if volume_policy not in {"tick_count", "zero"}:
        raise ValueError("volume_policy must be 'tick_count' or 'zero'")
    ingested = (ingested_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for row in rows:
        ts = _to_utc_datetime(row.get("ts_event") or row.get("ts") or row.get("event_time_ns"))
        bid_value = _first_present(row, ("bid_price_0", "bid_price", "bids_0_price"))
        ask_value = _first_present(row, ("ask_price_0", "ask_price", "asks_0_price"))
        if bid_value is None or ask_value is None:
            raise ValueError("depth row missing top bid/ask price columns")
        bid = _decode_number(bid_value, scale=price_scale)
        ask = _decode_number(ask_value, scale=price_scale)
        if not _valid_mid(bid, ask):
            continue
        buckets[_minute_floor(ts, bar_type)].append((bid + ask) / 2.0)
    return _bars_from_buckets(
        buckets,
        instrument_id=instrument_id,
        source="cffex_depth_mid_bar",
        bar_source="depth_mid",
        volume_policy=volume_policy,
        ingested_at=ingested,
    )


def _first_present(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _bars_from_buckets(
    buckets: dict[datetime, list[float]],
    *,
    instrument_id: str,
    source: str,
    bar_source: str,
    volume_policy: str,
    ingested_at: datetime,
) -> list[MidBar]:
    bars: list[MidBar] = []
    for ts, mids in sorted(buckets.items()):
        if not mids:
            continue
        tick_count = len(mids)
        volume = float(tick_count if volume_policy == "tick_count" else 0)
        bars.append(
            MidBar(
                ts=ts,
                instrument_id=instrument_id,
                open=float(mids[0]),
                high=float(max(mids)),
                low=float(min(mids)),
                close=float(mids[-1]),
                volume=volume,
                quote_volume=0.0,
                trade_count=tick_count,
                source=source,
                bar_source=bar_source,
                ingested_at=ingested_at,
            )
        )
    return bars


def symbol_for_partition(instrument_id: str) -> str:
    return instrument_id.split(".", 1)[0]


def partition_path(
    root: str | Path,
    *,
    exchange: str,
    venue_type: str,
    instrument_id: str,
    bar_type: str,
    date: str,
) -> Path:
    return (
        Path(root)
        / f"exchange={exchange}"
        / f"venue_type={venue_type}"
        / f"symbol={symbol_for_partition(instrument_id)}"
        / f"bar_type={bar_type}"
        / f"date={date}"
    )


def write_mid_bars(
    bars: list[MidBar],
    root: str | Path,
    *,
    exchange: str = "CFFEX",
    venue_type: str = "futures",
    bar_type: str = "1m",
) -> list[Path]:
    """Write bars to a Hive partitioned Parquet derived-data tree."""
    if not bars:
        return []
    import pyarrow as pa
    import pyarrow.parquet as pq

    by_partition: dict[tuple[str, str], list[MidBar]] = defaultdict(list)
    for bar in bars:
        by_partition[(bar.instrument_id, bar.ts.date().isoformat())].append(bar)

    written: list[Path] = []
    for (instrument_id, date), partition_bars in sorted(by_partition.items()):
        out_dir = partition_path(
            root,
            exchange=exchange,
            venue_type=venue_type,
            instrument_id=instrument_id,
            bar_type=bar_type,
            date=date,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "part-0.parquet"
        table = pa.Table.from_pylist([bar.to_dict() for bar in partition_bars])
        table = table.select([name for name in BAR_COLUMNS if name in table.column_names])
        pq.write_table(table, path)
        written.append(path)
    return written


def read_parquet_rows(paths: Iterable[Path], *, columns: list[str] | None = None) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(pq.read_table(path, columns=columns).to_pylist())
    return rows
