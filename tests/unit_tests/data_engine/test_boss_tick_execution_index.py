from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from data_engine.adapters.binance_vision_raw_trades import iter_raw_trade_archive
from scripts.internal.build_boss_tick_execution_index import (
    IndexedTrade,
    build_minute_index_rows,
    build_minute_index_rows_from_source,
    utc_day_start_ms,
    write_index_partition,
)


def trade(timestamp_ms: int, trade_id: int, row: int = 0) -> IndexedTrade:
    return IndexedTrade(
        event_time_ms=timestamp_ms,
        trade_id=trade_id,
        price=100.125,
        quantity=0.003,
        quote_quantity=0.375376,
        is_buyer_maker=False,
        source_row_index=row,
        source_date="2024-07-01",
        source_archive_name="fixture.zip",
        source_checksum="a" * 64,
    )


def test_minute_index_is_exact_and_same_millisecond_order_is_stable() -> None:
    day = date(2024, 7, 1)
    start = utc_day_start_ms(day)
    rows, summary = build_minute_index_rows(
        [
            trade(start + 125, 10, 0),
            trade(start + 125, 11, 1),
            trade(start + 60_050, 12, 2),
            trade(start + 86_399_999, 13, 3),
        ],
        day=day,
    )
    assert len(rows) == 1440
    assert summary["unresolved_boundaries"] == 0
    assert rows[0]["first_trade_id"] == 10
    assert rows[0]["wait_ms"] == 125
    assert rows[1]["first_trade_id"] == 12
    assert rows[1]["wait_ms"] == 50
    assert rows[-1]["first_trade_id"] == 13


def test_cross_day_first_trade_resolves_sparse_tail() -> None:
    day = date(2024, 7, 1)
    start = utc_day_start_ms(day)
    future = trade(start + 86_400_000 + 17, 99)
    rows, summary = build_minute_index_rows(
        [trade(start + 86_339_000, 1)], day=day, future_trade=future
    )
    assert summary["unresolved_boundaries"] == 0
    assert rows[-1]["first_trade_id"] == 99
    assert rows[-1]["wait_ms"] == 60_017


def test_unordered_official_source_still_builds_exact_first_trade_index() -> None:
    day = date(2024, 7, 1)
    start = utc_day_start_ms(day)
    rows, summary = build_minute_index_rows_from_source(
        [
            trade(start + 60_900, 13, 0),
            trade(start + 125, 10, 1),
            trade(start + 60_050, 12, 2),
            trade(start + 125, 11, 3),
            trade(start + 86_399_999, 14, 4),
        ],
        day=day,
    )
    assert summary["raw_trade_count"] == 5
    assert summary["unresolved_boundaries"] == 0
    assert rows[0]["first_trade_id"] == 10
    assert rows[1]["first_trade_id"] == 12
    assert rows[1]["source_row_index"] == 2
    assert rows[-1]["first_trade_id"] == 14


def test_atomic_parquet_contains_source_quote_quantity(tmp_path: Path) -> None:
    day = date(2024, 7, 1)
    start = utc_day_start_ms(day)
    rows, _ = build_minute_index_rows(
        [trade(start + 1, 1), trade(start + 86_399_999, 2)], day=day
    )
    destination = tmp_path / "part-0.parquet"
    digest = write_index_partition(destination, rows)
    assert len(digest) == 64
    assert pq.ParquetFile(destination).metadata.num_rows == 1440
    table = pq.read_table(destination, columns=["quote_quantity"])
    assert table.column(0)[0].as_py() == pytest.approx(0.375376)
    assert not destination.with_suffix(".parquet.tmp").exists()


def test_canonical_stream_parser_preserves_quote_qty_and_rejects_bad_order(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "trades.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "trades.csv",
            "id,price,qty,quote_qty,time,is_buyer_maker\n"
            "10,100.125,0.003,0.375376,1719792000000,false\n"
            "11,100.125,0.003,0.375377,1719792000000,true\n",
        )
    events = list(iter_raw_trade_archive(archive, symbol="BTCUSDT"))
    assert [event.trade_id for event in events] == [10, 11]
    assert [event.quote_quantity for event in events] == [0.375376, 0.375377]
    assert all(event.quote_quantity_source == "source_quote_qty" for event in events)

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as bundle:
        bundle.writestr(
            "trades.csv",
            "11,1,1,1,1719792000000,false\n10,1,1,1,1719792000000,false\n",
        )
    with pytest.raises(ValueError, match="source order violation"):
        list(iter_raw_trade_archive(bad, symbol="BTCUSDT"))
