"""Canonical Hive-Parquet source for perpetual funding settlements."""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from data_engine.events import FundingRateEvent
from data_engine.sources.hive_partitioning import (
    matching_fragments,
    select_date_window_fragments,
    validate_market_filters,
)
from data_engine.sources.parquet_bars import coerce_partition_date
from data_engine.time import to_event_time_ns, validate_time_unit
from data_engine.validation import require_numeric


class ParquetFundingSource:
    """Load funding settlements from the locked market-data layout."""

    def __init__(
        self,
        root: str,
        instrument_id: str,
        filters: dict[str, object],
        timestamp_column: str = "ts",
        timestamp_unit: str = "ns",
        rate_column: str = "funding_rate",
        interval_column: str = "funding_interval_hours",
        mark_price_column: str = "mark_price",
        start: object | None = None,
        end: object | None = None,
    ) -> None:
        validate_time_unit(timestamp_unit)
        self._root = root
        self._instrument_id = instrument_id
        self._filters = dict(filters)
        validate_market_filters(self._filters, data_type="funding_rate")
        self._timestamp_column = timestamp_column
        self._timestamp_unit = timestamp_unit
        self._rate_column = rate_column
        self._interval_column = interval_column
        self._mark_price_column = mark_price_column
        self._start = coerce_partition_date(start, "start")
        self._end = coerce_partition_date(end, "end")

    def stream(self) -> list[FundingRateEvent]:
        import pyarrow as pa
        import pyarrow.dataset as ds

        dataset = ds.dataset(self._root, format="parquet", partitioning="hive")
        fragments = matching_fragments(dataset, self._filters)
        if self._start is not None or self._end is not None:
            fragments = select_date_window_fragments(fragments, self._start, self._end)
        if not fragments:
            raise ValueError("no funding-rate fragments match the requested selector")
        names = set(fragments[0].physical_schema.names)
        for required in (self._timestamp_column, self._rate_column):
            if required not in names:
                raise ValueError(f"required funding column {required!r} is missing")
        columns = [self._timestamp_column, self._rate_column]
        columns += [c for c in (self._interval_column, self._mark_price_column) if c in names]
        tables = [fragment.to_table(columns=columns) for fragment in fragments]
        table = tables[0] if len(tables) == 1 else pa.concat_tables(tables)
        data = table.to_pydict()
        events = []
        for i in range(table.num_rows):
            ts = to_event_time_ns(data[self._timestamp_column][i], self._timestamp_unit)
            rate = require_numeric(data[self._rate_column][i], self._rate_column, i)
            interval = data.get(self._interval_column, [None] * table.num_rows)[i]
            mark = data.get(self._mark_price_column, [None] * table.num_rows)[i]
            events.append(FundingRateEvent(
                event_time_ns=ts,
                instrument_id=self._instrument_id,
                funding_rate=rate,
                interval_hours=int(interval) if interval is not None else None,
                mark_price=float(mark) if mark is not None else None,
                source="binance_vision_funding_rate",
            ))
        events.sort(key=lambda event: event.event_time_ns)
        if self._start is not None:
            start_ns = int(datetime.combine(
                self._start, time.min, timezone.utc,
            ).timestamp() * 1_000_000_000)
            events = [event for event in events if event.event_time_ns >= start_ns]
        return events


def load_parquet_funding(
    data_config: dict[str, Any],
) -> tuple[list[FundingRateEvent], list[FundingRateEvent]]:
    root = data_config.get("root") or data_config.get("path")
    if not root:
        raise ValueError("hive_parquet_funding mode requires a market_data 'root'")
    source = ParquetFundingSource(
        root=root,
        instrument_id=data_config.get("instrument_id", "BTCUSDT-PERP.BINANCE"),
        filters=data_config.get("filters") or {},
        timestamp_column=data_config.get("timestamp_column", "ts"),
        timestamp_unit=data_config.get("timestamp_unit", "ns"),
        rate_column=data_config.get("rate_column", "funding_rate"),
        interval_column=data_config.get("interval_column", "funding_interval_hours"),
        mark_price_column=data_config.get("mark_price_column", "mark_price"),
        start=data_config.get("start"),
        end=data_config.get("end"),
    )
    return [], source.stream()
