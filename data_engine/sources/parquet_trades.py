"""Hive-partitioned Parquet trade source (pyarrow only, no pandas).

The trade counterpart to ``parquet_bars`` — reads a StandardTrade Hive dataset
(e.g. produced by the Binance Vision aggTrades ingest) and returns plain
:class:`TradeEvent` objects.  Same design as the bar source: read once with
partition pruning + column pushdown, convert, sort by event time, split into
``warmup`` / ``live``.

Expected dataset layout::

    historical_data/market_data/exchange=BINANCE/venue_type=spot/
        symbol=BTCUSDT/data_type=aggTrades/date=2024-06-01/part-0.parquet

This module imports no ``nautilus_trader`` — it is part of the self-owned
``data_engine`` layer.
"""
from __future__ import annotations

from typing import Any

from data_engine.adapters.trade_adapter import make_trade_event, side_from_is_buyer_maker
from data_engine.events import TradeEvent
from data_engine.split import split_warmup_live
from data_engine.time import ONE_SECOND_NS, to_event_time_ns, validate_time_unit
from data_engine.validation import optional_numeric, require_numeric


def _equality_filter(filters: dict[str, Any] | None):
    """Turn ``{col: value}`` equality pairs into a combined pyarrow expression."""
    if not filters:
        return None
    import pyarrow.dataset as ds

    expr = None
    for column, value in filters.items():
        condition = ds.field(column) == value
        expr = condition if expr is None else (expr & condition)
    return expr


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
        trade_id_column: str | None = "agg_trade_id",
    ) -> None:
        validate_time_unit(timestamp_unit)
        self._root = root
        self._instrument_id = instrument_id
        self._warmup = warmup
        self._filters = dict(filters) if filters else {}
        self._timestamp_column = timestamp_column
        self._timestamp_unit = timestamp_unit
        self._price_column = price_column
        self._quantity_column = quantity_column
        self._quote_quantity_column = quote_quantity_column
        self._side_column = side_column
        self._is_buyer_maker_column = is_buyer_maker_column
        self._trade_id_column = trade_id_column
        self._trades: list[TradeEvent] | None = None

    def _row_to_trade(self, row: dict[str, Any], index: int) -> TradeEvent:
        price = require_numeric(row.get(self._price_column), self._price_column, index)
        quantity = require_numeric(row.get(self._quantity_column), self._quantity_column, index)

        quote_quantity: float | None = None
        if self._quote_quantity_column and row.get(self._quote_quantity_column) is not None:
            quote_quantity = optional_numeric(
                row[self._quote_quantity_column], price * quantity,
                self._quote_quantity_column, index,
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
            instrument_id=self._instrument_id,
            event_time_ns=event_time_ns,
            side=side,
            is_buyer_maker=is_buyer_maker,
            trade_id=trade_id,
            source="parquet_trades",
        )

    def _load_sorted(self) -> list[TradeEvent]:
        import pyarrow.dataset as ds

        dataset = ds.dataset(self._root, format="parquet", partitioning="hive")
        schema_names = set(dataset.schema.names)
        if self._price_column not in schema_names:
            raise ValueError(f"required price column {self._price_column!r} is missing")

        wanted = [
            self._timestamp_column,
            self._price_column,
            self._quantity_column,
            self._quote_quantity_column,
            self._side_column,
            self._is_buyer_maker_column,
            self._trade_id_column,
        ]
        columns = [c for c in wanted if c and c in schema_names]

        table = dataset.to_table(columns=columns, filter=_equality_filter(self._filters))
        data = table.to_pydict()
        trades = [
            self._row_to_trade({col: data[col][i] for col in columns}, i)
            for i in range(table.num_rows)
        ]
        trades.sort(key=lambda t: t.event_time_ns)
        return trades

    def _trades_cached(self) -> list[TradeEvent]:
        if self._trades is None:
            self._trades = self._load_sorted()
        return self._trades

    def warmup(self) -> list[TradeEvent]:
        return split_warmup_live(self._trades_cached(), self._warmup)[0]

    def stream(self) -> list[TradeEvent]:
        return split_warmup_live(self._trades_cached(), self._warmup)[1]


def load_parquet_trades(data_config: dict[str, Any]) -> tuple[list[TradeEvent], list[TradeEvent]]:
    """Build a :class:`ParquetTradeSource` from a config and return ``(warmup, live)``."""
    root = data_config.get("root") or data_config.get("path")
    if not root:
        raise ValueError("parquet_trades mode requires a 'root' directory for the Parquet dataset")
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
        trade_id_column=data_config.get("trade_id_column", "agg_trade_id"),
    )
    return split_warmup_live(source._trades_cached(), source._warmup)
