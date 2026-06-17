"""Hive-partitioned Parquet historical bar source (pyarrow only, no pandas).

This is the production-style counterpart to ``csv_bars`` — meant for larger
historical backtests stored as a Hive-partitioned Parquet dataset, e.g.::

    data/bars/trading_date=2024-01-01/instrument_id=BTC%2FUSDT/part-0.parquet

It reads with ``pyarrow.dataset`` and applies:

* **partition pruning / row filtering** — simple equality ``filters`` become a
  pyarrow expression so unmatched partitions are never read,
* **column pushdown** — only the bar columns we need are projected.

It shares the *design* of ``quant_feature_engine/storage/parquet_store.py`` but
deliberately has no dependency on it: this source is part of the canonical
``data_engine`` layer and returns plain :class:`BarEvent` objects.
"""
from __future__ import annotations

from typing import Any

from data_engine.adapters.bar_adapter import make_bar_event
from data_engine.events import BarEvent
from data_engine.sources.hive_partitioning import hive_partition_values, matching_fragments
from data_engine.split import split_warmup_live
from data_engine.time import ONE_SECOND_NS, to_event_time_ns, validate_time_unit
from data_engine.validation import optional_numeric, require_numeric

# Canonical bar timestamp column (CSV / replay-parity data). Binance Vision /
# Historical Data Manager bar parquet instead stores time in ``ts``
# (``timestamp[us]``), so the loader resolves between them — see
# :func:`resolve_bar_timestamp_column`.
_DEFAULT_TIMESTAMP_COLUMN = "event_time_ns"
_ALT_TIMESTAMP_COLUMN = "ts"


def resolve_bar_timestamp_column(schema_names, configured: str | None) -> str:
    """Resolve which column holds the bar timestamp (backward compatible).

    Rules:

    1. an explicit ``configured`` column that **exists** -> use it (config wins);
    2. the default ``event_time_ns`` that is **absent**, when ``ts`` exists ->
       use ``ts`` (the real Binance Vision / Hive bar schema);
    3. any other ``configured`` column that is absent -> return it unchanged,
       preserving the existing monotonic-index fallback in ``_row_to_bar``;
    4. ``configured is None`` -> auto-detect ``event_time_ns`` then ``ts``.

    This keeps ``event_time_ns`` datasets working exactly as before while making
    ``ts``-based bar parquet load with real timestamps instead of falling back to
    ``index x 1s`` (which produced 1970-epoch times).
    """
    names = set(schema_names)
    if configured is not None:
        if configured in names:
            return configured
        if configured == _DEFAULT_TIMESTAMP_COLUMN and _ALT_TIMESTAMP_COLUMN in names:
            return _ALT_TIMESTAMP_COLUMN
        return configured  # custom absent column -> keep existing fallback behavior
    if _DEFAULT_TIMESTAMP_COLUMN in names:
        return _DEFAULT_TIMESTAMP_COLUMN
    if _ALT_TIMESTAMP_COLUMN in names:
        return _ALT_TIMESTAMP_COLUMN
    return _DEFAULT_TIMESTAMP_COLUMN


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


def filter_fragments_by_date_range(fragments, start, end):
    """Keep only fragments whose Hive ``date=`` partition is within the inclusive
    ``[start, end]`` window (``start``/``end`` are ``date`` or ``None``).

    Physical, fragment-level pruning — applied **before** any ``to_table`` read,
    so out-of-window partitions are never read. A fragment missing a ``date``
    partition while a date bound is set is an error (no silent full read).
    """
    out = []
    for frag in fragments:
        parts = hive_partition_values(frag.path)
        if "date" not in parts:
            raise ValueError(
                "date-range filtering requested but a matching fragment has no "
                f"'date' partition: {frag.path}"
            )
        d = coerce_partition_date(parts["date"], "partition date")
        if start is not None and d < start:
            continue
        if end is not None and d > end:
            continue
        out.append(frag)
    return out


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
        partition_cols: list[str] | tuple[str, ...] | None = None,
        filters: dict[str, object] | None = None,
        timestamp_column: str = "event_time_ns",
        timestamp_unit: str = "ns",
        close_column: str = "close",
        open_column: str | None = "open",
        high_column: str | None = "high",
        low_column: str | None = "low",
        volume_column: str | None = "volume",
        start: object | None = None,
        end: object | None = None,
    ) -> None:
        validate_time_unit(timestamp_unit)  # fail fast, even if column is absent
        self._root = root
        self._instrument_id = instrument_id
        self._warmup_bars = warmup_bars
        self._partition_cols = tuple(partition_cols) if partition_cols else ()
        self._filters = dict(filters) if filters else {}
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
            instrument_id=self._instrument_id,
            event_time_ns=event_time_ns,
        )

    def _load_sorted(self) -> list[BarEvent]:
        import pyarrow as pa
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

        # Optional inclusive date-range window: prune to fragments whose Hive
        # ``date=`` partition falls in [start, end] *before* any read.
        if self._start is not None or self._end is not None:
            fragments = filter_fragments_by_date_range(fragments, self._start, self._end)
            if not fragments:
                raise ValueError(
                    f"no parquet fragments under {self._root!r} match filters "
                    f"{self._filters!r} within date range [{self._start}, {self._end}]"
                )

        # Schema guard based on the matching bar fragments, not the mixed root.
        schema_names = set(fragments[0].physical_schema.names)
        if self._close_column not in schema_names:
            raise ValueError(f"required close column {self._close_column!r} is missing")

        # Resolve the real timestamp column: respect an explicit config, fall back
        # from the default ``event_time_ns`` to ``ts`` for Binance Vision/Hive bar
        # parquet, and otherwise keep the monotonic-index fallback behavior.
        ts_col = resolve_bar_timestamp_column(schema_names, self._timestamp_column)

        # Column pushdown: project only the bar columns that actually exist.
        wanted = [
            ts_col,
            self._close_column,
            self._open_column,
            self._high_column,
            self._low_column,
            self._volume_column,
        ]
        columns = [c for c in wanted if c and c in schema_names]

        tables = [fragment.to_table(columns=columns) for fragment in fragments]
        table = tables[0] if len(tables) == 1 else pa.concat_tables(tables)
        data = table.to_pydict()  # column-oriented; no pandas
        bars = [
            self._row_to_bar({col: data[col][i] for col in columns}, i, ts_col=ts_col)
            for i in range(table.num_rows)
        ]
        bars.sort(key=lambda b: b.event_time_ns)
        return bars

    def _bars_cached(self) -> list[BarEvent]:
        if self._bars is None:
            self._bars = self._load_sorted()
        return self._bars

    def warmup(self) -> list[BarEvent]:
        return split_warmup_live(self._bars_cached(), self._warmup_bars)[0]

    def stream(self) -> list[BarEvent]:
        return split_warmup_live(self._bars_cached(), self._warmup_bars)[1]


def load_parquet_bars(data_config: dict[str, Any]) -> tuple[list[BarEvent], list[BarEvent]]:
    """Build a :class:`ParquetBarSource` from a config and return ``(warmup, live)``."""
    root = data_config.get("root") or data_config.get("path")
    if not root:
        raise ValueError("parquet_bars mode requires a 'root' directory for the Parquet dataset")
    source = ParquetBarSource(
        root=root,
        instrument_id=data_config.get("instrument_id", "BTC/USDT"),
        warmup_bars=int(data_config.get("warmup_bars", 0)),
        partition_cols=data_config.get("partition_cols"),
        filters=data_config.get("filters"),
        timestamp_column=data_config.get("timestamp_column", "event_time_ns"),
        timestamp_unit=data_config.get("timestamp_unit", "ns"),
        close_column=data_config.get("close_column", "close"),
        open_column=data_config.get("open_column", "open"),
        high_column=data_config.get("high_column", "high"),
        low_column=data_config.get("low_column", "low"),
        volume_column=data_config.get("volume_column", "volume"),
        start=data_config.get("start"),
        end=data_config.get("end"),
    )
    return split_warmup_live(source._bars_cached(), source._warmup_bars)
