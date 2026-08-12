from __future__ import annotations

import math
import zipfile
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from data_engine.adapters.binance_vision_raw_trades import read_raw_trade_archive
from data_engine.adapters.trade_adapter import make_trade_event
from data_engine.sources.parquet_trades import ParquetTradeSource
from data_engine.transforms import aggregate_ticks_to_bars
from data_engine.transforms import resample_standard_bars
from scripts.internal.build_1s_bars_from_ticks import bar_partition_path
from scripts.internal.build_1s_bars_from_ticks import summarize_bar_rows
from scripts.internal.build_1s_bars_from_ticks import summarize_trade_partition
from scripts.internal.build_1s_bars_from_ticks import summarize_trades
from scripts.internal.build_1s_bars_from_ticks import trade_partition_path
from scripts.internal.build_1s_bars_from_ticks import validate_bar_totals
from scripts.internal.build_1s_bars_from_ticks import validate_tick_summaries
from scripts.internal.build_1s_bars_from_ticks import write_standard_bar_partition
from scripts.internal.build_1s_bars_from_ticks import write_trade_partition


DAY = date(2024, 1, 1)
DAY_START_MS = 1_704_067_200_000


def _raw_archive(tmp_path: Path) -> tuple[Path, list[tuple]]:
    # Deliberately include source quoteQty values that are not identical to the
    # binary-float product. IDs 1/2 are reversed in the CSV but share one ms.
    rows = [
        (2, "0.1", "0.2", "0.02000001", DAY_START_MS + 100, "false"),
        (1, "0.3", "0.4", "0.11999999", DAY_START_MS + 100, "true"),
        (3, "10.0", "0.5", "5.00000001", DAY_START_MS + 999, "false"),
        (4, "11.0", "0.25", "2.74999999", DAY_START_MS + 1_000, "true"),
        # second +2 is intentionally empty
        (5, "12.0", "0.1", "1.20000001", DAY_START_MS + 3_000, "false"),
        (6, "13.0", "0.2", "2.59999999", DAY_START_MS + 59_999, "true"),
        (7, "14.0", "0.3", "4.20000001", DAY_START_MS + 60_000, "false"),
    ]
    archive = tmp_path / "BTCUSDT-trades-2024-01-01.zip"
    csv_text = "id,price,qty,quote_qty,time,is_buyer_maker\n" + "\n".join(
        ",".join(str(value) for value in row) for row in rows
    )
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("BTCUSDT-trades-2024-01-01.csv", csv_text)
    return archive, rows


def _assert_rows_equal(left: list[dict], right: list[dict]) -> None:
    fields = (
        "ts_event",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    )
    assert len(left) == len(right)
    for left_row, right_row in zip(left, right, strict=True):
        for field in fields:
            if field in {"ts_event", "trade_count"}:
                assert left_row[field] == right_row[field]
            else:
                assert math.isclose(left_row[field], right_row[field], rel_tol=1e-15, abs_tol=1e-15)


def test_raw_trade_parser_preserves_source_quote_qty_and_deterministic_order(
    tmp_path: Path,
) -> None:
    archive, rows = _raw_archive(tmp_path)
    trades = read_raw_trade_archive(archive, symbol="BTCUSDT")

    assert len(trades) == len(rows)
    assert [trade.trade_id for trade in trades] == [1, 2, 3, 4, 5, 6, 7]
    assert all(
        trade.event_time_ns == int(row[4]) * 1_000_000
        for trade, row in zip(trades, sorted(rows), strict=True)
    )
    assert trades[1].quote_quantity == float(rows[0][3])
    assert trades[1].quote_quantity != trades[1].price * trades[1].quantity
    assert all(trade.quote_quantity_source == "source_quote_qty" for trade in trades)
    assert [trade.side for trade in trades[:2]] == ["SELL", "BUY"]


def test_tick_persistence_schema_reader_and_atomic_rerun(tmp_path: Path) -> None:
    archive, _ = _raw_archive(tmp_path)
    trades = read_raw_trade_archive(archive, symbol="BTCUSDT")
    market_root = tmp_path / "historical_data" / "market_data"

    path = write_trade_partition(market_root=market_root, day=DAY, trades=trades, batch_size=2)
    source_summary = summarize_trades(trades, DAY)
    persisted_summary = summarize_trade_partition(path)
    validate_tick_summaries(source_summary, persisted_summary)

    assert path == trade_partition_path(market_root, DAY)
    assert pq.ParquetFile(path).schema_arrow.names == [
        "ts",
        "instrument_id",
        "trade_id",
        "price",
        "quantity",
        "quote_quantity",
        "quote_quantity_source",
        "is_buyer_maker",
        "side",
        "source",
        "ingested_at",
    ]
    stray = path.with_suffix(".parquet.tmp")
    stray.write_bytes(b"interrupted")
    assert list(path.parent.glob("*.parquet")) == [path]

    # A rerun atomically replaces the canonical file and leaves it readable.
    assert (
        write_trade_partition(market_root=market_root, day=DAY, trades=trades, batch_size=3) == path
    )
    assert pq.ParquetFile(path).metadata.num_rows == len(trades)

    source = ParquetTradeSource(
        root=str(market_root),
        instrument_id="BTCUSDT",
        filters={
            "asset_class": "crypto",
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": "BTCUSDT",
            "data_type": "trade",
            "freq": "tick",
        },
    )
    loaded = source.stream()
    assert [trade.trade_id for trade in loaded] == list(range(1, 8))
    assert [trade.quote_quantity for trade in loaded] == [trade.quote_quantity for trade in trades]


def test_source_quote_qty_drives_1s_bar_fields_without_empty_bar(tmp_path: Path) -> None:
    archive, _ = _raw_archive(tmp_path)
    trades = read_raw_trade_archive(archive, symbol="BTCUSDT")
    result = aggregate_ticks_to_bars(trades, frequency="1s", default_instrument="BTCUSDT")
    source_summary = summarize_trades(trades, DAY)
    bar_summary = summarize_bar_rows(result.rows)
    validate_bar_totals(tick_summary=source_summary, bar_summary=bar_summary, frequency="1s")

    assert result.quote_quantity_fallback_count == 0
    assert bar_summary["trade_count"] == len(trades)
    assert math.isclose(bar_summary["quote_volume"], math.fsum(t.quote_quantity for t in trades))
    assert math.isclose(
        bar_summary["taker_buy_quote_volume"],
        math.fsum(t.quote_quantity for t in trades if t.is_buyer_maker is False),
    )
    seconds = {row["ts_event"] // 1_000_000_000 for row in result.rows}
    assert DAY_START_MS // 1_000 + 2 not in seconds
    assert len(result.rows) == 5


def test_direct_and_resampled_subminute_bars_are_equivalent(tmp_path: Path) -> None:
    archive, _ = _raw_archive(tmp_path)
    trades = read_raw_trade_archive(archive, symbol="BTCUSDT")
    one_second = aggregate_ticks_to_bars(trades, frequency="1s", default_instrument="BTCUSDT")
    for frequency in ("5s", "15s"):
        direct = aggregate_ticks_to_bars(trades, frequency=frequency, default_instrument="BTCUSDT")
        resampled = resample_standard_bars(
            one_second.rows,
            frequency=frequency,
            default_instrument="BTCUSDT",
        )
        _assert_rows_equal(direct.rows, resampled.rows)


def test_standard_bar_hive_path_and_schema_are_compatible(tmp_path: Path) -> None:
    archive, _ = _raw_archive(tmp_path)
    trades = read_raw_trade_archive(archive, symbol="BTCUSDT")
    result = aggregate_ticks_to_bars(trades, frequency="1s", default_instrument="BTCUSDT")
    market_root = tmp_path / "historical_data" / "market_data"
    path = write_standard_bar_partition(
        market_root=market_root,
        day=DAY,
        frequency="1s",
        rows=result.rows,
    )
    assert path == bar_partition_path(market_root, DAY, "1s")
    assert pq.ParquetFile(path).schema_arrow.names == [
        "ts",
        "instrument_id",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "source",
        "ingested_at",
    ]


def test_quote_quantity_fallback_is_explicit() -> None:
    trade = make_trade_event(
        price=0.1,
        quantity=0.2,
        instrument_id="BTCUSDT",
        event_time_ns=1,
    )
    assert trade.quote_quantity == trade.price * trade.quantity
    assert trade.quote_quantity_source == "price_x_quantity_fallback"
    result = aggregate_ticks_to_bars([trade], frequency="1s")
    assert result.quote_quantity_fallback_count == 1
