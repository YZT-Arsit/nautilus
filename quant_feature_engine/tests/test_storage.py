"""Storage layer: round-trip through Hive Parquet + manifest semantics."""
from __future__ import annotations

import polars as pl

from quant_feature_engine.storage.layout import PartitionKey, parse_partition_path
from quant_feature_engine.storage.metadata import Manifest, params_hash
from quant_feature_engine.storage.parquet_store import ParquetStore


def test_partition_path_round_trip() -> None:
    key = PartitionKey.from_dict(
        {"feature_group": "technical", "frequency": "1m", "trading_date": "2026-05-26"},
        order=("feature_group", "frequency", "trading_date"),
    )
    p = key.to_path("/data/features")
    assert str(p).endswith("feature_group=technical/frequency=1m/trading_date=2026-05-26")
    parsed = parse_partition_path(p)
    assert parsed == {
        "feature_group": "technical",
        "frequency": "1m",
        "trading_date": "2026-05-26",
    }


def test_parquet_store_round_trip(tmp_path) -> None:
    store = ParquetStore(
        tmp_path,
        partition_cols=("feature_group", "frequency", "trading_date"),
    )
    df = pl.DataFrame(
        {
            "symbol": ["AAA", "AAA", "BBB"],
            "ts_event": [1, 2, 3],
            "sma_20": [10.0, 11.0, 12.0],
        }
    )
    store.write(
        df,
        partition_values={
            "feature_group": "technical",
            "frequency": "1m",
            "trading_date": "2026-05-26",
        },
    )
    loaded = store.scan(
        filters={
            "feature_group": "technical",
            "frequency": "1m",
            "trading_date": "2026-05-26",
        },
        columns=["symbol", "ts_event", "sma_20"],
    )
    assert loaded.height == 3
    assert sorted(loaded["sma_20"].to_list()) == [10.0, 11.0, 12.0]


def test_manifest_dedup(tmp_path) -> None:
    m = Manifest(tmp_path)
    ph = params_hash({"window": 20})
    m.append(
        [
            {
                "partition_key": "feature_group=technical/frequency=1m/trading_date=2026-05-26",
                "feature_name": "sma_20",
                "version": 1,
                "params_hash": ph,
                "row_count": 100,
                "source": "backfill",
            }
        ]
    )
    assert m.has(
        "feature_group=technical/frequency=1m/trading_date=2026-05-26",
        "sma_20",
        1,
        ph,
    )
    assert not m.has(
        "feature_group=technical/frequency=1m/trading_date=2026-05-26",
        "sma_20",
        2,  # different version
        ph,
    )
