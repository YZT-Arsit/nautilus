"""FeatureDataReader: 历史特征数据查询接口。"""
from __future__ import annotations

import polars as pl

from feature_engine.storage import FeatureDataReader, Manifest, ParquetStore
from feature_engine.storage.metadata import params_hash


def _write_feature_partition(
    root,
    *,
    feature_group: str,
    frequency: str,
    trading_date: str,
    df: pl.DataFrame,
) -> None:
    store = ParquetStore(
        root, partition_cols=("feature_group", "frequency", "trading_date")
    )
    store.write(
        df,
        partition_values={
            "feature_group": feature_group,
            "frequency": frequency,
            "trading_date": trading_date,
        },
    )


def test_scan_features_partition_pruning(tmp_path) -> None:
    root = tmp_path / "feature_data"
    df1 = pl.DataFrame(
        {
            "symbol": ["IH2303.CFFEX", "IH2303.CFFEX"],
            "ts_event": [1, 2],
            "sma_20": [10.0, 11.0],
        }
    )
    df2 = pl.DataFrame(
        {
            "symbol": ["IH2303.CFFEX"],
            "ts_event": [3],
            "sma_20": [99.0],
        }
    )
    _write_feature_partition(
        root,
        feature_group="technical",
        frequency="1m",
        trading_date="2026-05-26",
        df=df1,
    )
    _write_feature_partition(
        root,
        feature_group="technical",
        frequency="1m",
        trading_date="2026-05-27",
        df=df2,
    )

    reader = FeatureDataReader(root)
    one = reader.scan_features(trading_date="2026-05-26", frequency="1m")
    assert one.height == 2
    assert sorted(one["sma_20"].to_list()) == [10.0, 11.0]
    # 分区列保留，便于下游辨认来源。
    assert "trading_date" in one.columns


def test_scan_features_instrument_filter(tmp_path) -> None:
    root = tmp_path / "feature_data"
    df = pl.DataFrame(
        {
            "symbol": ["AAA", "BBB", "AAA"],
            "ts_event": [1, 2, 3],
            "sma_20": [1.0, 2.0, 3.0],
        }
    )
    _write_feature_partition(
        root,
        feature_group="technical",
        frequency="1m",
        trading_date="2026-05-26",
        df=df,
    )
    reader = FeatureDataReader(root)
    only_aaa = reader.scan_features(
        trading_date="2026-05-26", frequency="1m", instrument_id="AAA"
    )
    assert only_aaa.height == 2
    assert set(only_aaa["symbol"].to_list()) == {"AAA"}


def test_scan_features_column_projection(tmp_path) -> None:
    root = tmp_path / "feature_data"
    df = pl.DataFrame(
        {"symbol": ["AAA"], "ts_event": [1], "sma_20": [1.0], "rsi_14": [50.0]}
    )
    _write_feature_partition(
        root,
        feature_group="technical",
        frequency="1m",
        trading_date="2026-05-26",
        df=df,
    )
    reader = FeatureDataReader(root)
    got = reader.scan_features(
        trading_date="2026-05-26", columns=["symbol", "sma_20"]
    )
    assert set(got.columns) == {"symbol", "sma_20"}


def test_scan_features_empty_root_returns_empty(tmp_path) -> None:
    reader = FeatureDataReader(tmp_path / "nonexistent")
    got = reader.scan_features(trading_date="2026-05-26")
    assert got.is_empty()


def test_available_features_from_manifest(tmp_path) -> None:
    feature_root = tmp_path / "feature_data"
    manifest_root = tmp_path / "manifests"
    df = pl.DataFrame({"symbol": ["AAA"], "ts_event": [1], "sma_20": [1.0]})
    _write_feature_partition(
        feature_root,
        feature_group="technical",
        frequency="1m",
        trading_date="2026-05-26",
        df=df,
    )
    m = Manifest(manifest_root)
    m.append(
        [
            {
                "partition_key": "feature_group=technical/frequency=1m/trading_date=2026-05-26",
                "feature_name": "sma_20",
                "version": 1,
                "params_hash": params_hash({"window": 20}),
                "row_count": 1,
                "source": "backfill",
            }
        ]
    )
    reader = FeatureDataReader(feature_root, manifest_root=manifest_root)
    avail = reader.available_features(trading_date="2026-05-26", frequency="1m")
    assert "feature_name" in avail.columns
    assert avail["feature_name"].to_list() == ["sma_20"]


def test_available_features_inferred_without_manifest(tmp_path) -> None:
    feature_root = tmp_path / "feature_data"
    df = pl.DataFrame(
        {"symbol": ["AAA"], "ts_event": [1], "sma_20": [1.0], "rsi_14": [50.0]}
    )
    _write_feature_partition(
        feature_root,
        feature_group="technical",
        frequency="1m",
        trading_date="2026-05-26",
        df=df,
    )
    reader = FeatureDataReader(feature_root)  # 无 manifest
    avail = reader.available_features(trading_date="2026-05-26")
    feats = set(avail["feature_name"].to_list())
    # 骨架列 symbol / ts_event 被排除，只剩特征列。
    assert feats == {"sma_20", "rsi_14"}
