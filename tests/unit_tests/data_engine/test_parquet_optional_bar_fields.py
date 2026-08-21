from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from data_engine.sources.parquet_bars import ParquetBarSource


def test_mixed_optional_bar_schemas_are_read_without_reconstructing_fields(tmp_path) -> None:
    filters = {
        "asset_class": "crypto",
        "exchange": "BINANCE",
        "venue_type": "futures_um",
        "symbol": "BTCUSDT",
        "data_type": "bar",
        "freq": "1m",
    }
    locked_root = tmp_path
    for key, value in filters.items():
        locked_root /= f"{key}={value}"
    old_partition = locked_root / "date=2024-01-01"
    new_partition = locked_root / "date=2024-01-02"
    old_partition.mkdir(parents=True)
    new_partition.mkdir(parents=True)
    base = {
        "ts": [datetime(2024, 1, 1, tzinfo=timezone.utc)],
        "open": [100.0],
        "high": [101.0],
        "low": [99.0],
        "close": [100.5],
        "volume": [2.0],
    }
    pq.write_table(pa.table(base), old_partition / "part-0.parquet")
    pq.write_table(
        pa.table(
            {
                **base,
                "ts": [datetime(2024, 1, 2, tzinfo=timezone.utc)],
                "quote_volume": [201.25],
                "trade_count": [7],
                "taker_buy_volume": [1.25],
                "taker_buy_quote_volume": [125.75],
            }
        ),
        new_partition / "part-0.parquet",
    )

    bars = ParquetBarSource(
        root=tmp_path,
        instrument_id="BTCUSDT",
        filters=filters,
    ).stream()

    assert len(bars) == 2
    assert bars[0].quote_volume is None
    assert bars[0].taker_buy_volume is None
    assert bars[1].quote_volume == 201.25
    assert bars[1].trade_count == 7
    assert bars[1].taker_buy_volume == 1.25
    assert bars[1].taker_buy_quote_volume == 125.75
