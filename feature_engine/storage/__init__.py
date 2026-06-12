"""特征存储层：Hive-style Parquet 读写、manifest 与历史特征查询。

公共接口::

    from feature_engine.storage import (
        ParquetStore,
        Manifest,
        params_hash,
        PartitionKey,
        parse_partition_path,
        FeatureDataReader,
    )

``EodArchiver`` 位于 ``feature_engine.streaming.archiver``，因为它属于流式
归档路径，这里不重复导出以避免在 ``import feature_engine.storage`` 时拉入
流式依赖。
"""
from feature_engine.storage.feature_reader import (
    DEFAULT_FEATURE_PARTITION_COLS,
    FeatureDataReader,
)
from feature_engine.storage.layout import PartitionKey, parse_partition_path
from feature_engine.storage.metadata import Manifest, params_hash
from feature_engine.storage.parquet_store import ParquetStore

__all__ = [
    "ParquetStore",
    "Manifest",
    "params_hash",
    "PartitionKey",
    "parse_partition_path",
    "FeatureDataReader",
    "DEFAULT_FEATURE_PARTITION_COLS",
]
