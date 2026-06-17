"""Read-only validators for locally cached partitions (pyarrow only, no pandas).

``validate_partition`` reads a single partition's parquet file(s) and returns a
structured :class:`ValidationResult`.  Never downloads, never writes parquet.

pyarrow is imported lazily inside the functions so the rest of the historical
package stays importable without it.  Imports no ``feature_engine`` or
``nautilus_trader``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_engine.historical.catalog import partition_dir

BAR_REQUIRED = ["ts", "open", "high", "low", "close", "volume", "instrument_id", "source"]
TRADE_REQUIRED = [
    "ts", "price", "quantity", "quote_quantity", "side", "is_buyer_maker",
    "agg_trade_id", "instrument_id", "source",
]


@dataclass
class ValidationResult:
    ok: bool
    data_kind: str
    path: str
    row_count: int = 0
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def _read_partition_table(pdir: Path):
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415

    files = sorted(pdir.glob("*.parquet"))
    if not files:
        return None, []
    tables = [pq.read_table(str(f)) for f in files]
    table = tables[0] if len(tables) == 1 else pa.concat_tables(tables)
    return table, files


def validate_partition(
    *,
    root: str | Path,
    exchange: str,
    venue_type: str,
    symbol: str,
    data_kind: str,
    date: str,
    bar_type: str | None = None,
    data_type: str | None = None,
) -> ValidationResult:
    """Read-only validation of one partition. Returns a structured result; never
    downloads and never writes."""
    import pyarrow.compute as pc  # noqa: PLC0415

    if data_kind == "trade" and data_type is None:
        data_type = "aggTrades"
    pdir = partition_dir(
        root, exchange=exchange, venue_type=venue_type, symbol=symbol,
        data_kind=data_kind, date=date, bar_type=bar_type, data_type=data_type,
    )
    errors: list[str] = []
    details: dict[str, Any] = {}

    if not pdir.exists():
        return ValidationResult(False, data_kind, str(pdir), 0, [f"partition dir missing: {pdir}"])

    table, files = _read_partition_table(pdir)
    if table is None:
        return ValidationResult(False, data_kind, str(pdir), 0, ["no parquet file in partition"])

    details["file_count"] = len(files)
    cols = set(table.column_names)
    required = BAR_REQUIRED if data_kind == "bar" else TRADE_REQUIRED
    missing_cols = [c for c in required if c not in cols]
    if missing_cols:
        errors.append(f"missing required columns: {missing_cols}")

    n = table.num_rows
    details["row_count"] = n
    if n == 0:
        errors.append("row_count is 0")

    if "ts" in cols and n > 0:
        ts = table.column("ts")
        details["ts_min"] = str(pc.min(ts).as_py())
        details["ts_max"] = str(pc.max(ts).as_py())

    if n > 0 and not missing_cols:
        if data_kind == "bar":
            for f in ("open", "high", "low", "close", "volume"):
                nulls = table.column(f).null_count
                if nulls:
                    errors.append(f"{f} has {nulls} null(s)")
            details["close_range"] = [pc.min(table.column("close")).as_py(),
                                      pc.max(table.column("close")).as_py()]
            details["high_range"] = [pc.min(table.column("high")).as_py(),
                                     pc.max(table.column("high")).as_py()]
            details["low_range"] = [pc.min(table.column("low")).as_py(),
                                    pc.max(table.column("low")).as_py()]
            details["volume_range"] = [pc.min(table.column("volume")).as_py(),
                                       pc.max(table.column("volume")).as_py()]
        else:  # trade
            details["price_range"] = [pc.min(table.column("price")).as_py(),
                                      pc.max(table.column("price")).as_py()]
            details["quantity_range"] = [pc.min(table.column("quantity")).as_py(),
                                         pc.max(table.column("quantity")).as_py()]
            details["quote_quantity_nulls"] = table.column("quote_quantity").null_count
            side_counts = {}
            for r in pc.value_counts(table.column("side")).to_pylist():
                side_counts[str(r["values"])] = r["counts"]
            details["side_distribution"] = side_counts
            aid = table.column("agg_trade_id")
            dup = n - pc.count_distinct(aid).as_py()
            details["duplicate_agg_trade_id"] = dup
            if dup > 0:
                errors.append(f"{dup} duplicate agg_trade_id(s)")

    return ValidationResult(
        ok=not errors, data_kind=data_kind, path=str(pdir),
        row_count=n, errors=errors, details=details,
    )
