"""历史特征数据查询接口（FeatureDataReader）。

历史特征数据（``feature_data``）和历史行情数据（``market_data``）一样，是
历史数据体系的一部分：一旦计算落盘，就应当能被训练、回测、实盘 warmup 等
下游环节按分区高效复用。

本模块在 :class:`~feature_engine.storage.parquet_store.ParquetStore` 之上提供
一个只读的、面向查询的薄封装：

* :meth:`FeatureDataReader.scan_features` —— 按 ``trading_date`` /
  ``frequency`` / ``feature_group`` 做分区裁剪，按 ``instrument_id`` 做列级
  过滤，并支持列投影。不指定 ``feature_group`` 时，会把各 group 合并成一个
  “每个 (symbol, ts_event) 一行、特征列齐全”的特征矩阵。
* :meth:`FeatureDataReader.available_features` —— 回答“某个分区下有哪些特征
  可用”，优先读 :class:`~feature_engine.storage.metadata.Manifest`，没有
  manifest 时退化为从数据列名推断。

分区约定
--------
默认特征数据的 Hive 分区列是新版平级布局
``(feature_group, asset_class, exchange, frequency, trading_date, instrument_id)``。
读层仍兼容旧布局 ``(feature_group, frequency, trading_date)``：目录缺少的新分区
列不会被强制要求，``instrument_id`` 查询会回落到数据体中的 ``symbol`` /
``instrument_id`` 列过滤。

为什么按 group 分别扫描再 concat
--------------------------------
不同 ``feature_group`` 落在不同分区目录，且各自的特征列不同（technical 有
sma/rsi，volume 有 vwm）。如果用一个 PyArrow dataset 一次性扫描跨 group 的根
目录，union 出来的 schema 可能只采用第一个 fragment，导致只存在于某个 group 的
列（如 ``vwm_20``）被丢弃。为此这里**枚举 ``feature_group=*`` 目录，对每个
group 用其子根目录单独扫描（schema 同构）**，再用
``pl.concat(..., how="diagonal_relaxed")`` 合并——不同 schema 由 Polars 取列
并集、缺失列 null 补齐对齐，所有特征列都会保留——最后
:meth:`_merge_feature_groups` 把跨 group 的 null 补齐行折叠成
“一行/(symbol, ts_event)”。

不依赖 pandas，仅使用 Polars / PyArrow。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from feature_engine.storage.layout import FEATURE_DATA_PARTITION_COLS
from feature_engine.storage.metadata import Manifest
from feature_engine.storage.parquet_store import ParquetStore

# 默认使用新版 feature_data 平级布局。
DEFAULT_FEATURE_PARTITION_COLS: tuple[str, ...] = FEATURE_DATA_PARTITION_COLS

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
        partition_cols : 特征数据的 Hive 分区列顺序，默认新版平级布局。旧布局可
        显式传入，也可在默认 reader 下被兼容读取。
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
        # feature_group 之后的分区列（用于子根扫描），如 (frequency, trading_date)。
        self._sub_partition_cols = tuple(
            c for c in self.partition_cols if c != "feature_group"
        )

    # ------------------------------------------------------------------ scan

    def scan_features(
        self,
        *,
        date: str | None = None,
        symbol: str | None = None,
        freq: str | None = None,
        feature_group: str | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """读取历史特征数据，返回一个干净的特征矩阵。

        ``date`` / ``freq`` / ``feature_group`` 做分区裁剪，``symbol`` 做分区/
        列级过滤（按可用列匹配），``columns`` 做列投影。不指定 ``feature_group``
        时跨 group 合并成一行/(symbol, ts_event)。
        """
        if "feature_group" in self.partition_cols:
            groups = (
                [feature_group]
                if feature_group is not None
                else self._discover_feature_groups()
            )
            df = self._scan_groups(groups, freq, date)
        else:
            df = self._scan_single(feature_group, freq, date)

        if df.is_empty():
            return df

        if symbol is not None:
            df = self._filter_instrument(df, symbol)

        # 跨 feature_group 的 null 补齐行 -> 一行/(symbol, ts_event)。
        df = self._merge_feature_groups(df)

        if columns is not None:
            keep = [c for c in columns if c in df.columns]
            df = df.select(keep)
        return df

    def _discover_feature_groups(self) -> list[str]:
        """枚举 ``feature_root`` 下已存在的 ``feature_group=*`` 目录名。"""
        groups: list[str] = []
        if self.feature_root.exists():
            for child in sorted(self.feature_root.iterdir()):
                if child.is_dir() and child.name.startswith("feature_group="):
                    groups.append(child.name.split("=", 1)[1])
        return groups

    def _scan_groups(
        self,
        groups: list[str],
        freq: str | None,
        date: str | None,
    ) -> pl.DataFrame:
        """逐个 feature_group 扫描其子根目录（schema 同构），vertical_relaxed 合并。"""
        frames: list[pl.DataFrame] = []
        for group in groups:
            sub_root = self.feature_root / f"feature_group={group}"
            if not sub_root.exists():
                continue
            sub_store = ParquetStore(sub_root, self._sub_partition_cols)
            active = {}
            if date is not None and "date" in self._sub_partition_cols:
                active["date"] = date
            if freq is not None and "freq" in self._sub_partition_cols:
                active["freq"] = freq
            f = sub_store.scan(filters=active or None, drop_partition_cols=False)
            if f.is_empty():
                continue
            # 重新挂回被窄化剥离的分区信息，保证跨 group 列一致、可被 merge 识别。
            f = f.with_columns(pl.lit(group).alias("feature_group"))
            if freq is not None and "freq" not in f.columns:
                f = f.with_columns(pl.lit(freq).alias("freq"))
            if date is not None and "date" not in f.columns:
                f = f.with_columns(pl.lit(date).alias("date"))
            frames.append(f)

        if not frames:
            return pl.DataFrame()
        # 各 group 列集不同（technical: sma/rsi, volume: vwm），用 diagonal_relaxed
        # 取列并集、缺失列 null 补齐（vertical_relaxed 只放宽 dtype、要求列集相同）。
        return pl.concat(frames, how="diagonal_relaxed")

    def _scan_single(
        self,
        feature_group: str | None,
        freq: str | None,
        date: str | None,
    ) -> pl.DataFrame:
        """无 feature_group 维度的布局：直接按分区过滤扫描。"""
        wanted = {
            "feature_group": feature_group,
            "freq": freq,
            "date": date,
        }
        active = {
            k: v
            for k, v in wanted.items()
            if v is not None and k in self.partition_cols
        }
        return self._store.scan(filters=active or None, drop_partition_cols=False)

    def _merge_feature_groups(self, df: pl.DataFrame) -> pl.DataFrame:
        """把跨 feature_group 的 null 补齐行合并成一行/(symbol, ts_event)。

        仅在结果确实跨越多个 feature_group 时触发；单 group 查询原样返回（保留
        分区列）。合并后丢弃 ``feature_group`` 列，但把单值的其它分区列
        （如 trading_date / frequency）重新挂回，保持列的可用性。
        """
        if "feature_group" not in df.columns:
            return df
        id_key = "symbol" if "symbol" in df.columns else (
            "instrument_id" if "instrument_id" in df.columns else None
        )
        keys = [c for c in (id_key, "ts_event") if c is not None and c in df.columns]
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
        date: str | None = None,
        symbol: str | None = None,
        freq: str | None = None,
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
                if date is not None:
                    mdf = mdf.filter(
                        pl.col("partition_key").str.contains(
                            f"date={date}", literal=True
                        )
                    )
                if freq is not None:
                    mdf = mdf.filter(
                        pl.col("partition_key").str.contains(
                            f"freq={freq}", literal=True
                        )
                    )
                return mdf

        # 退化路径：从数据列名推断特征名。
        data = self.scan_features(date=date, symbol=symbol, freq=freq)
        feats = [
            c
            for c in data.columns
            if c not in _NON_FEATURE_COLUMNS and c not in self.partition_cols
        ]
        return pl.DataFrame({"feature_name": feats})
