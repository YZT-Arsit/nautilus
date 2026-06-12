"""历史特征数据查询接口（FeatureDataReader）。

历史特征数据（``feature_data``）和历史行情数据（``market_data``）一样，是
历史数据体系的一部分：一旦计算落盘，就应当能被训练、回测、实盘 warmup 等
下游环节按分区高效复用。

本模块在 :class:`~feature_engine.storage.parquet_store.ParquetStore` 之上提供
一个只读的、面向查询的薄封装：

* :meth:`FeatureDataReader.scan_features` —— 按 ``trading_date`` /
  ``frequency`` / ``feature_group`` 做分区裁剪，按 ``instrument_id`` 做列级
  过滤，并支持列投影。
* :meth:`FeatureDataReader.available_features` —— 回答“某个分区下有哪些特征
  可用”，优先读 :class:`~feature_engine.storage.metadata.Manifest`，没有
  manifest 时退化为从数据列名推断。

分区约定
--------
当前特征数据的 Hive 分区列是 ``(feature_group, frequency, trading_date)``，
与写入侧（``EodArchiver`` / ``ParquetStore``）保持一致。``instrument_id``
维度当前**不在分区路径里**，而是作为数据体的 ``symbol`` 列存在（由
``data_engine`` 的 ``bars_to_polars`` 把 ``instrument_id`` 映射为 ``symbol``）。
因此按 ``instrument_id`` 查询时，本类在扫描结果上对 ``symbol`` 列做等值过滤。
未来若把 instrument 提升为分区维度，只需扩展 ``partition_cols``，查询接口的
签名保持不变。

不依赖 pandas，仅使用 Polars / PyArrow。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from feature_engine.storage.metadata import Manifest
from feature_engine.storage.parquet_store import ParquetStore

# 默认特征分区列，与 EodArchiver / 既有 test_storage 写入侧一致。
DEFAULT_FEATURE_PARTITION_COLS: tuple[str, ...] = (
    "feature_group",
    "frequency",
    "trading_date",
)

# 这些列是“骨架列”而非特征本身，available_features 推断时需要排除。
_NON_FEATURE_COLUMNS = frozenset(
    {"symbol", "instrument_id", "ts_event", "ts_init", "event_time_ns"}
)


class FeatureDataReader:
    """历史特征数据的只读查询入口。

    Parameters
    ----------
    feature_root : 特征数据根目录（例如 ``historical_data/feature_data``）。
    manifest_root : manifest 根目录。给定时 :meth:`available_features` 会优先
        从 manifest 返回特征清单；为 ``None`` 时退化为从数据列名推断。
    partition_cols : 特征数据的 Hive 分区列顺序，默认
        ``("feature_group", "frequency", "trading_date")``。必须与写入侧一致。
    """

    def __init__(
        self,
        feature_root: str | Path,
        manifest_root: str | Path | None = None,
        partition_cols: tuple[str, ...] = DEFAULT_FEATURE_PARTITION_COLS,
    ) -> None:
        self.feature_root = Path(feature_root)
        self.partition_cols = tuple(partition_cols)
        self._store = ParquetStore(self.feature_root, self.partition_cols)
        self._manifest = Manifest(manifest_root) if manifest_root is not None else None

    # ------------------------------------------------------------------ scan

    def scan_features(
        self,
        *,
        trading_date: str | None = None,
        instrument_id: str | None = None,
        frequency: str | None = None,
        feature_group: str | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """读取历史特征数据。

        ``trading_date`` / ``frequency`` / ``feature_group`` 中凡是属于分区列
        的，都会下推为 Hive 分区裁剪（只读命中的分区目录）。``instrument_id``
        作为列级过滤（对 ``symbol`` 列等值匹配）。``columns`` 用于列投影。

        返回的 DataFrame 保留分区列，方便下游辨认每行来自哪个分区。
        """
        part_filters = {
            "feature_group": feature_group,
            "frequency": frequency,
            "trading_date": trading_date,
        }
        # 只保留“非 None 且确实是分区列”的过滤条件。
        active = {
            k: v
            for k, v in part_filters.items()
            if v is not None and k in self.partition_cols
        }

        df = self._store.scan(
            filters=active or None,
            drop_partition_cols=False,
        )
        if df.is_empty():
            return df

        if instrument_id is not None:
            df = self._filter_instrument(df, instrument_id)

        # 特征按 feature_group 分区落盘（如 technical / volume），跨 group 扫描会
        # 得到“每个 group 一份、互相 null 补齐”的行。对训练/回测/warmup 复用而言，
        # 期望的是“每个 (symbol, ts_event) 一行、所有特征列齐全”的特征矩阵，
        # 因此在这里把多个 feature_group 合并回一行。
        df = self._merge_feature_groups(df)

        if columns is not None:
            keep = [c for c in columns if c in df.columns]
            df = df.select(keep)
        return df

    def _merge_feature_groups(self, df: pl.DataFrame) -> pl.DataFrame:
        """把跨 feature_group 的 null 补齐行合并成一行/(symbol, ts_event)。

        仅在结果确实跨越多个 feature_group 时触发；单 group 查询原样返回（保留
        分区列）。合并后丢弃 ``feature_group`` 列，但把单值的其它分区列
        （如 trading_date / frequency）重新挂回，保持列的可用性。
        """
        if "feature_group" not in df.columns:
            return df
        keys = [c for c in ("symbol", "ts_event") if c in df.columns]
        if not keys or df["feature_group"].n_unique() <= 1:
            return df

        # 记下单值的其它分区列，合并后重新挂回。
        carry = {
            c: df[c][0]
            for c in self.partition_cols
            if c != "feature_group" and c in df.columns and df[c].n_unique() == 1
        }
        feature_cols = [
            c for c in df.columns if c not in keys and c not in self.partition_cols
        ]
        merged = df.group_by(keys, maintain_order=True).agg(
            [pl.col(c).drop_nulls().first().alias(c) for c in feature_cols]
        )
        if carry:
            merged = merged.with_columns([pl.lit(v).alias(k) for k, v in carry.items()])
        return merged

    def _filter_instrument(self, df: pl.DataFrame, instrument_id: str) -> pl.DataFrame:
        """按标的过滤。优先匹配 ``symbol`` 列，其次 ``instrument_id`` 列。"""
        for col in ("symbol", "instrument_id"):
            if col in df.columns:
                return df.filter(pl.col(col) == instrument_id)
        raise ValueError(
            "当前特征数据没有 'symbol' / 'instrument_id' 列，无法按 instrument_id "
            "过滤；该维度需在写入侧补齐后才能查询。"
        )

    # ------------------------------------------------------------- available

    def available_features(
        self,
        *,
        trading_date: str | None = None,
        instrument_id: str | None = None,
        frequency: str | None = None,
    ) -> pl.DataFrame:
        """返回某个分区下可用的特征清单。

        有 manifest 时返回 manifest 记录（含 ``feature_name`` / ``version`` /
        ``params_hash`` / ``computed_at`` / ``row_count`` / ``source``），并按
        ``trading_date`` / ``frequency`` 在 ``partition_key`` 上做子串过滤。

        没有 manifest（或 manifest 为空）时，退化为扫描数据并把非骨架列当作
        特征名返回，列为单列 ``feature_name``。
        """
        if self._manifest is not None:
            mdf = self._manifest.read()
            if not mdf.is_empty():
                if trading_date is not None:
                    mdf = mdf.filter(
                        pl.col("partition_key").str.contains(
                            f"trading_date={trading_date}", literal=True
                        )
                    )
                if frequency is not None:
                    mdf = mdf.filter(
                        pl.col("partition_key").str.contains(
                            f"frequency={frequency}", literal=True
                        )
                    )
                return mdf

        # 退化路径：从数据列名推断特征名。
        data = self.scan_features(
            trading_date=trading_date,
            instrument_id=instrument_id,
            frequency=frequency,
        )
        feats = [
            c
            for c in data.columns
            if c not in _NON_FEATURE_COLUMNS and c not in self.partition_cols
        ]
        return pl.DataFrame({"feature_name": feats})
