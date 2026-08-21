"""Hive-partitioned Parquet historical bar source (pyarrow only, no pandas).

This is the production historical source for the locked market-data dataset::

    historical_data/market_data/asset_class=crypto/exchange=BINANCE/
        venue_type=futures_um/symbol=BTCUSDT/data_type=bar/freq=1m/
        date=2024-07-01/part-0.parquet

It reads with ``pyarrow.dataset`` and applies:

* **partition pruning / row filtering** — simple equality ``filters`` become a
  pyarrow expression so unmatched partitions are never read,
* **column pushdown** — only the bar columns we need are projected.

It is the canonical read path and returns plain :class:`BarEvent` objects.
"""
from __future__ import annotations

from typing import Any

from data_engine.adapters.bar_adapter import make_bar_event
from data_engine.events import BarEvent
from data_engine.sources.hive_partitioning import (
    matching_fragments,
    select_date_window_fragments,
    validate_market_filters,
)
from data_engine.split import split_warmup_live
from data_engine.time import ONE_SECOND_NS, to_event_time_ns, validate_time_unit
from data_engine.validation import optional_numeric, require_numeric

def coerce_partition_date(value, label: str = "date"):
    """Coerce ``value`` to a ``datetime.date`` (accepts ``YYYY-MM-DD`` strings,
    ``date``/``datetime``). Raises ValueError with a clear message otherwise."""
    from datetime import date as _date, datetime as _datetime

    if value is None:
        return None
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    if isinstance(value, str):
        try:
            return _datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"invalid {label} {value!r}; expected YYYY-MM-DD") from None
    raise ValueError(f"invalid {label} {value!r}; expected a YYYY-MM-DD string or date")


class ParquetBarSource:
    """Loads bars from a Hive-partitioned Parquet dataset.

    Rows are read once (with pushdown), converted to :class:`BarEvent`, sorted by
    event time, then split into ``warmup``/``live`` — never sorted inside the
    engine's ``on_event()``.
    """

    def __init__(
        self,
        root: str,
        instrument_id: str,
        warmup_bars: int = 0,
        filters: dict[str, object] | None = None,
        timestamp_column: str = "ts",
        timestamp_unit: str = "ns",
        close_column: str = "close",
        open_column: str | None = "open",
        high_column: str | None = "high",
        low_column: str | None = "low",
        volume_column: str | None = "volume",
        quote_volume_column: str | None = "quote_volume",
        trade_count_column: str | None = "trade_count",
        taker_buy_volume_column: str | None = "taker_buy_volume",
        taker_buy_quote_volume_column: str | None = "taker_buy_quote_volume",
        start: object | None = None,
        end: object | None = None,
    ) -> None:
        validate_time_unit(timestamp_unit)  # fail fast, even if column is absent
        self._root = root
        self._instrument_id = instrument_id
        self._warmup_bars = warmup_bars
        self._filters = dict(filters) if filters else {}
        validate_market_filters(self._filters, data_type="bar")
        # Optional inclusive date-range window (fragment-level pruning). Parsed
        # eagerly so an invalid YYYY-MM-DD fails fast at construction.
        self._start = coerce_partition_date(start, "start")
        self._end = coerce_partition_date(end, "end")
        self._timestamp_column = timestamp_column
        self._timestamp_unit = timestamp_unit
        self._close_column = close_column
        self._open_column = open_column
        self._high_column = high_column
        self._low_column = low_column
        self._volume_column = volume_column
        self._quote_volume_column = quote_volume_column
        self._trade_count_column = trade_count_column
        self._taker_buy_volume_column = taker_buy_volume_column
        self._taker_buy_quote_volume_column = taker_buy_quote_volume_column
        self._bars: list[BarEvent] | None = None  # cache: read the dataset once

    def _row_to_bar(self, row: dict[str, Any], index: int, ts_col: str | None = None) -> BarEvent:
        close_col = self._close_column
        close = require_numeric(row.get(close_col), close_col, index)

        def _opt(col: str | None, default: float) -> float:
            if col and row.get(col) is not None:
                return optional_numeric(row[col], default, col, index)
            return default

        ts_col = ts_col if ts_col is not None else self._timestamp_column
        if ts_col and row.get(ts_col) is not None:
            try:
                event_time_ns = to_event_time_ns(row[ts_col], self._timestamp_unit)
            except ValueError as exc:
                raise ValueError(f"row {index}: {exc}") from None
        else:
            event_time_ns = index * ONE_SECOND_NS  # monotonic fallback

        return make_bar_event(
            close=close,
            open=_opt(self._open_column, close),
            high=_opt(self._high_column, close),
            low=_opt(self._low_column, close),
            volume=_opt(self._volume_column, 0.0),
            quote_volume=(
                _opt(self._quote_volume_column, 0.0)
                if self._quote_volume_column and row.get(self._quote_volume_column) is not None
                else None
            ),
            trade_count=(
                int(row[self._trade_count_column])
                if self._trade_count_column and row.get(self._trade_count_column) is not None
                else None
            ),
            taker_buy_volume=(
                _opt(self._taker_buy_volume_column, 0.0)
                if self._taker_buy_volume_column and row.get(self._taker_buy_volume_column) is not None
                else None
            ),
            taker_buy_quote_volume=(
                _opt(self._taker_buy_quote_volume_column, 0.0)
                if self._taker_buy_quote_volume_column and row.get(self._taker_buy_quote_volume_column) is not None
                else None
            ),
            instrument_id=self._instrument_id,
            event_time_ns=event_time_ns,
        )

    def _load_sorted(self) -> list[BarEvent]:
        import pyarrow.dataset as ds

        dataset = ds.dataset(self._root, format="parquet", partitioning="hive")

        # Restrict to the filter-matching bar fragments *before* the schema guard.
        # Under a unified ``market_data`` root, the global dataset schema may be
        # inferred from a trade fragment (no ``close`` column); selecting the bar
        # fragments first makes the guard and projection accurate.
        fragments = matching_fragments(dataset, self._filters)
        if not fragments:
            raise ValueError(
                f"no parquet fragments under {self._root!r} match filters {self._filters!r}"
            )

        # Live data is restricted to [start, end]. Warmup comes from partitions
        # immediately before start, never from the beginning of the live window.
        if self._start is not None or self._end is not None:
            fragments = select_date_window_fragments(
                fragments, self._start, self._end, self._warmup_bars,
            )
            if not fragments:
                raise ValueError(
                    f"no parquet fragments under {self._root!r} match filters "
                    f"{self._filters!r} within date range [{self._start}, {self._end}]"
                )

        # Column pushdown is resolved per fragment.  Historical partitions can
        # legitimately pre-date additive optional fields (for example Binance
        # taker-buy volumes), while the required OHLCV contract remains stable.
        # Projecting the first fragment's optional schema across every fragment
        # makes Arrow reject such mixed-schema datasets.
        ts_col = self._timestamp_column
        wanted = [
            ts_col,
            self._close_column,
            self._open_column,
            self._high_column,
            self._low_column,
            self._volume_column,
            self._quote_volume_column,
            self._trade_count_column,
            self._taker_buy_volume_column,
            self._taker_buy_quote_volume_column,
        ]
        bars: list[BarEvent] = []
        row_index = 0
        for fragment in fragments:
            schema_names = set(fragment.physical_schema.names)
            if self._close_column not in schema_names:
                raise ValueError(
                    f"required close column {self._close_column!r} is missing from "
                    f"fragment {fragment.path!r}"
                )
            if ts_col not in schema_names:
                raise ValueError(
                    f"required timestamp column {ts_col!r} is missing from "
                    f"fragment {fragment.path!r}"
                )
            columns = [c for c in wanted if c and c in schema_names]
            table = fragment.to_table(columns=columns)
            data = table.to_pydict()  # column-oriented; no pandas
            for i in range(table.num_rows):
                bars.append(
                    self._row_to_bar(
                        {col: data[col][i] for col in columns},
                        row_index,
                        ts_col=ts_col,
                    )
                )
                row_index += 1
        bars.sort(key=lambda b: b.event_time_ns)
        return bars

    def _bars_cached(self) -> list[BarEvent]:
        if self._bars is None:
            self._bars = self._load_sorted()
        return self._bars

    def warmup(self) -> list[BarEvent]:
        return self._split_cached()[0]

    def stream(self) -> list[BarEvent]:
        return self._split_cached()[1]

    def _split_cached(self) -> tuple[list[BarEvent], list[BarEvent]]:
        bars = self._bars_cached()
        if self._start is None:
            return split_warmup_live(bars, self._warmup_bars)
        from datetime import datetime, time, timezone  # noqa: PLC0415
        start_ns = int(datetime.combine(self._start, time.min, timezone.utc).timestamp() * 1_000_000_000)
        prior = [bar for bar in bars if bar.event_time_ns < start_ns]
        live = [bar for bar in bars if bar.event_time_ns >= start_ns]
        return prior[-self._warmup_bars:] if self._warmup_bars else [], live


def load_parquet_bars(data_config: dict[str, Any]) -> tuple[list[BarEvent], list[BarEvent]]:
    """Build a :class:`ParquetBarSource` from a config and return ``(warmup, live)``."""
    root = data_config.get("root") or data_config.get("path")
    if not root:
        raise ValueError("hive_parquet_bars mode requires a market_data 'root'")
    source = ParquetBarSource(
        root=root,
        instrument_id=data_config.get("instrument_id", "BTC/USDT"),
        warmup_bars=int(data_config.get("warmup_bars", 0)),
        filters=data_config.get("filters"),
        timestamp_column=data_config.get("timestamp_column", "ts"),
        timestamp_unit=data_config.get("timestamp_unit", "ns"),
        close_column=data_config.get("close_column", "close"),
        open_column=data_config.get("open_column", "open"),
        high_column=data_config.get("high_column", "high"),
        low_column=data_config.get("low_column", "low"),
        volume_column=data_config.get("volume_column", "volume"),
        quote_volume_column=data_config.get("quote_volume_column", "quote_volume"),
        trade_count_column=data_config.get("trade_count_column", "trade_count"),
        taker_buy_volume_column=data_config.get("taker_buy_volume_column", "taker_buy_volume"),
        taker_buy_quote_volume_column=data_config.get(
            "taker_buy_quote_volume_column", "taker_buy_quote_volume"
        ),
        start=data_config.get("start"),
        end=data_config.get("end"),
    )
    return source.warmup(), source.stream()
