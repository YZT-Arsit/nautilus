"""FeatureDataReader: 历史特征数据查询接口。"""
from __future__ import annotations

import polars as pl

from feature_engine.storage import FeatureDataReader, Manifest, ParquetStore
from feature_engine.storage.layout import FEATURE_DATA_PARTITION_COLS
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


def _write_new_feature_partition(
    root,
    *,
    feature_group: str,
    asset_class: str,
    exchange: str,
    frequency: str,
    trading_date: str,
    instrument_id: str,
    df: pl.DataFrame,
) -> None:
    store = ParquetStore(root, partition_cols=FEATURE_DATA_PARTITION_COLS)
    store.write(
        df,
        partition_values={
            "feature_group": feature_group,
            "asset_class": asset_class,
            "exchange": exchange,
            "frequency": frequency,
            "trading_date": trading_date,
            "instrument_id": instrument_id,
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


def test_scan_features_across_feature_groups(tmp_path) -> None:
    """不指定 feature_group 时，technical 和 volume 两组特征列都应返回。"""
    root = tmp_path / "feature_data"
    # technical group: sma_20 / rsi_14
    _write_feature_partition(
        root,
        feature_group="technical",
        frequency="1m",
        trading_date="2026-05-26",
        df=pl.DataFrame(
            {
                "symbol": ["IH2303.CFFEX", "IH2303.CFFEX", "IH2303.CFFEX"],
                "ts_event": [1, 2, 3],
                "sma_20": [10.0, 11.0, 12.0],
                "rsi_14": [40.0, 50.0, 60.0],
            }
        ),
    )
    # volume group: vwm_20（不同 schema，落在不同分区目录）
    _write_feature_partition(
        root,
        feature_group="volume",
        frequency="1m",
        trading_date="2026-05-26",
        df=pl.DataFrame(
            {
                "symbol": ["IH2303.CFFEX", "IH2303.CFFEX", "IH2303.CFFEX"],
                "ts_event": [1, 2, 3],
                "vwm_20": [0.1, 0.2, 0.3],
            }
        ),
    )

    reader = FeatureDataReader(root)
    df = reader.scan_features(trading_date="2026-05-26", frequency="1m")

    # 关键：跨 group 读取时 volume 分区的 vwm_20 不能丢。
    assert "vwm_20" in df.columns
    assert "sma_20" in df.columns
    assert "rsi_14" in df.columns
    # 折叠成一行/(symbol, ts_event)，3 个时间戳 -> 3 行。
    assert df.height == 3
    # 同一行同时拿到 technical 和 volume 的特征值。
    row = df.sort("ts_event").row(0, named=True)
    assert row["sma_20"] == 10.0
    assert row["vwm_20"] == 0.1


def test_scan_features_new_layout_instrument_filter_and_group_merge(tmp_path) -> None:
    """新版布局：instrument_id 是分区列，跨 feature_group 查询仍合并成特征矩阵。"""
    root = tmp_path / "feature_data"
    for instrument_id, base in [("AAA.CFFEX", 10.0), ("BBB.CFFEX", 20.0)]:
        _write_new_feature_partition(
            root,
            feature_group="technical",
            asset_class="future",
            exchange="CFFEX",
            frequency="1m",
            trading_date="2026-05-26",
            instrument_id=instrument_id,
            df=pl.DataFrame(
                {
                    "symbol": [instrument_id, instrument_id],
                    "ts_event": [1, 2],
                    "sma_20": [base, base + 1.0],
                }
            ),
        )
        _write_new_feature_partition(
            root,
            feature_group="volume",
            asset_class="future",
            exchange="CFFEX",
            frequency="1m",
            trading_date="2026-05-26",
            instrument_id=instrument_id,
            df=pl.DataFrame(
                {
                    "symbol": [instrument_id, instrument_id],
                    "ts_event": [1, 2],
                    "vwm_20": [base / 100.0, (base + 1.0) / 100.0],
                }
            ),
        )

    reader = FeatureDataReader(root)
    df = reader.scan_features(
        trading_date="2026-05-26", frequency="1m", instrument_id="AAA.CFFEX"
    ).sort("ts_event")

    assert df.height == 2
    assert set(df["symbol"].to_list()) == {"AAA.CFFEX"}
    assert "sma_20" in df.columns
    assert "vwm_20" in df.columns
    assert df.row(0, named=True)["sma_20"] == 10.0
    assert df.row(0, named=True)["vwm_20"] == 0.1


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
