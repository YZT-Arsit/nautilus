"""Hive-partitioned Parquet trade source (pyarrow only, no pandas).

The trade counterpart to ``parquet_bars`` — reads a StandardTrade Hive dataset
(e.g. produced by the Binance Vision aggTrades ingest) and returns plain
:class:`TradeEvent` objects.  Same design as the bar source: read once with
partition pruning + column pushdown, convert, sort by event time, split into
``warmup`` / ``live``.

Expected dataset layout (locked canonical partitioning)::

    historical_data/market_data/asset_class=crypto/exchange=BINANCE/
        venue_type=spot/symbol=BTCUSDT/data_type=trade/freq=tick/
        date=2024-06-01/part-0.parquet

This module imports no ``nautilus_trader`` — it is part of the self-owned
``data_engine`` layer.
"""

from __future__ import annotations

from typing import Any

from data_engine.adapters.trade_adapter import make_trade_event
from data_engine.adapters.trade_adapter import side_from_is_buyer_maker
from data_engine.events import TradeEvent
from data_engine.sources.hive_partitioning import matching_fragments
from data_engine.sources.hive_partitioning import select_date_window_fragments
from data_engine.sources.hive_partitioning import validate_market_filters
from data_engine.sources.parquet_bars import coerce_partition_date
from data_engine.split import split_warmup_live
from data_engine.time import ONE_SECOND_NS
from data_engine.time import to_event_time_ns
from data_engine.time import validate_time_unit
from data_engine.validation import optional_numeric
from data_engine.validation import require_numeric


class ParquetTradeSource:
    """Loads trades from a Hive-partitioned StandardTrade Parquet dataset."""

    def __init__(
        self,
        root: str,
        instrument_id: str,
        warmup: int = 0,
        filters: dict[str, object] | None = None,
        timestamp_column: str = "ts",
        timestamp_unit: str = "ns",
        price_column: str = "price",
        quantity_column: str = "quantity",
        quote_quantity_column: str | None = "quote_quantity",
        side_column: str | None = "side",
        is_buyer_maker_column: str | None = "is_buyer_maker",
        trade_id_column: str | None = None,
        quote_quantity_source_column: str | None = "quote_quantity_source",
        start: object | None = None,
        end: object | None = None,
    ) -> None:
        validate_time_unit(timestamp_unit)
        self._root = root
        self._instrument_id = instrument_id
        self._warmup = warmup
        self._filters = dict(filters) if filters else {}
        validate_market_filters(self._filters, data_type="trade")
        self._start = coerce_partition_date(start, "start")
        self._end = coerce_partition_date(end, "end")
        self._timestamp_column = timestamp_column
        self._timestamp_unit = timestamp_unit
        self._price_column = price_column
        self._quantity_column = quantity_column
        self._quote_quantity_column = quote_quantity_column
        self._side_column = side_column
        self._is_buyer_maker_column = is_buyer_maker_column
        self._trade_id_column = trade_id_column
        self._quote_quantity_source_column = quote_quantity_source_column
        self._trades: list[TradeEvent] | None = None

    def _row_to_trade(self, row: dict[str, Any], index: int) -> TradeEvent:
        price = require_numeric(row.get(self._price_column), self._price_column, index)
        quantity = require_numeric(row.get(self._quantity_column), self._quantity_column, index)

        quote_quantity: float | None = None
        if self._quote_quantity_column and row.get(self._quote_quantity_column) is not None:
            quote_quantity = optional_numeric(
                row[self._quote_quantity_column],
                price * quantity,
                self._quote_quantity_column,
                index,
            )

        is_buyer_maker = None
        if self._is_buyer_maker_column and row.get(self._is_buyer_maker_column) is not None:
            is_buyer_maker = bool(row[self._is_buyer_maker_column])

        side = None
        if self._side_column and row.get(self._side_column) is not None:
            side = str(row[self._side_column])
        else:
            side = side_from_is_buyer_maker(is_buyer_maker)

        trade_id = None
        if self._trade_id_column and row.get(self._trade_id_column) is not None:
            trade_id = row[self._trade_id_column]

        quote_quantity_source = None
        if (
            self._quote_quantity_source_column
            and row.get(self._quote_quantity_source_column) is not None
        ):
            quote_quantity_source = str(row[self._quote_quantity_source_column])

        ts_col = self._timestamp_column
        if ts_col and row.get(ts_col) is not None:
            try:
                event_time_ns = to_event_time_ns(row[ts_col], self._timestamp_unit)
            except ValueError as exc:
                raise ValueError(f"row {index}: {exc}") from None
        else:
            event_time_ns = index * ONE_SECOND_NS

        return make_trade_event(
            price=price,
            quantity=quantity,
            quote_quantity=quote_quantity,
            quote_quantity_source=quote_quantity_source,
            instrument_id=self._instrument_id,
            event_time_ns=event_time_ns,
            side=side,
            is_buyer_maker=is_buyer_maker,
            trade_id=trade_id,
            source="parquet_trades",
        )

    def _load_sorted(self) -> list[TradeEvent]:
        import pyarrow as pa
        import pyarrow.dataset as ds

        dataset = ds.dataset(self._root, format="parquet", partitioning="hive")

        # Restrict to the filter-matching trade fragments *before* any schema
        # check.  In a unified ``market_data`` root, the global dataset schema
        # may be inferred from a bar fragment (no ``price`` column); selecting
        # the trade fragments first makes the guard accurate.
        fragments = matching_fragments(dataset, self._filters)
        if not fragments:
            raise ValueError(
                f"no parquet fragments under {self._root!r} match filters {self._filters!r}"
            )
        if self._start is not None or self._end is not None:
            fragments = select_date_window_fragments(
                fragments,
                self._start,
                self._end,
                self._warmup,
            )
            if not fragments:
                raise ValueError("no trade fragments match the requested date window")

        # Schema guard based on the matching trade fragments, not the mixed root.
        schema_names = set(fragments[0].physical_schema.names)
        if self._price_column not in schema_names:
            raise ValueError(f"required price column {self._price_column!r} is missing")
        if self._quantity_column not in schema_names:
            raise ValueError(f"required quantity column {self._quantity_column!r} is missing")
        if self._timestamp_column not in schema_names:
            raise ValueError(f"required timestamp column {self._timestamp_column!r} is missing")

        # Raw Binance trades use ``trade_id``. The legacy aggTrades dataset used
        # ``agg_trade_id``. Auto-detect only when the caller did not explicitly
        # configure a column, preserving compatibility during schema evolution.
        if self._trade_id_column is None:
            if "trade_id" in schema_names:
                self._trade_id_column = "trade_id"
            elif "agg_trade_id" in schema_names:
                self._trade_id_column = "agg_trade_id"

        wanted = [
            self._timestamp_column,
            self._price_column,
            self._quantity_column,
            self._quote_quantity_column,
            self._side_column,
            self._is_buyer_maker_column,
            self._trade_id_column,
            self._quote_quantity_source_column,
        ]
        columns = [c for c in wanted if c and c in schema_names]

        tables = [fragment.to_table(columns=columns) for fragment in fragments]
        table = tables[0] if len(tables) == 1 else pa.concat_tables(tables)
        data = table.to_pydict()
        trades = [
            self._row_to_trade({col: data[col][i] for col in columns}, i)
            for i in range(table.num_rows)
        ]

        def _trade_id_key(trade: TradeEvent) -> tuple[int, object]:
            value = trade.trade_id
            if value is None:
                return (2, "")
            try:
                return (0, int(value))
            except (TypeError, ValueError):
                return (1, str(value))

        trades.sort(key=lambda trade: (trade.event_time_ns, _trade_id_key(trade)))
        return trades

    def _trades_cached(self) -> list[TradeEvent]:
        if self._trades is None:
            self._trades = self._load_sorted()
        return self._trades

    def warmup(self) -> list[TradeEvent]:
        return self._split_cached()[0]

    def stream(self) -> list[TradeEvent]:
        return self._split_cached()[1]

    def _split_cached(self) -> tuple[list[TradeEvent], list[TradeEvent]]:
        trades = self._trades_cached()
        if self._start is None:
            return split_warmup_live(trades, self._warmup)
        from datetime import datetime  # noqa: PLC0415
        from datetime import time  # noqa: PLC0415
        from datetime import timezone  # noqa: PLC0415

        start_ns = int(
            datetime.combine(self._start, time.min, timezone.utc).timestamp() * 1_000_000_000
        )
        prior = [trade for trade in trades if trade.event_time_ns < start_ns]
        live = [trade for trade in trades if trade.event_time_ns >= start_ns]
        return prior[-self._warmup :] if self._warmup else [], live


def load_parquet_trades(data_config: dict[str, Any]) -> tuple[list[TradeEvent], list[TradeEvent]]:
    """Build a :class:`ParquetTradeSource` from a config and return ``(warmup, live)``."""
    root = data_config.get("root") or data_config.get("path")
    if not root:
        raise ValueError("hive_parquet_trades mode requires a market_data 'root'")
    source = ParquetTradeSource(
        root=root,
        instrument_id=data_config.get("instrument_id", "BTC/USDT"),
        warmup=int(data_config.get("warmup", data_config.get("warmup_trades", 0))),
        filters=data_config.get("filters"),
        timestamp_column=data_config.get("timestamp_column", "ts"),
        timestamp_unit=data_config.get("timestamp_unit", "ns"),
        price_column=data_config.get("price_column", "price"),
        quantity_column=data_config.get("quantity_column", "quantity"),
        quote_quantity_column=data_config.get("quote_quantity_column", "quote_quantity"),
        side_column=data_config.get("side_column", "side"),
        is_buyer_maker_column=data_config.get("is_buyer_maker_column", "is_buyer_maker"),
        trade_id_column=data_config.get("trade_id_column"),
        quote_quantity_source_column=data_config.get(
            "quote_quantity_source_column",
            "quote_quantity_source",
        ),
        start=data_config.get("start"),
        end=data_config.get("end"),
    )
    return source.warmup(), source.stream()
