#!/usr/bin/env python
"""历史行情数据 -> 历史特征数据 的端到端构建入口。

把一段历史行情（来自 Hive market_data 根目录，或指定的 CSV / Parquet 文件）
读出来，用 ``feature_engine`` 计算特征，再把特征数据按 Hive-style Parquet 落到
``feature_data`` 并写 manifest。

数据流
------
::

    market_data (Hive / CSV / Parquet)
        -> data_engine BarEvent
        -> data_engine.bars_to_polars            (Part 4 桥接)
        -> feature_engine FeatureDAG + StreamingEngine
        -> enriched DataFrame (raw + feature 列)
        -> feature_engine EodArchiver
        -> feature_data/ (Hive Parquet) + manifests/

示例
----
::

    python scripts/build_historical_features.py \\
        --market-root data/historical/market_data \\
        --feature-root data/historical/feature_data \\
        --manifest-root data/historical/manifests \\
        --trading-date 2026-05-26 \\
        --instrument-id IH2303.CFFEX \\
        --frequency 1m \\
        --features sma_20,rsi_14,vwm_20 \\
        --mode overwrite

真实可用的 feature 名称来自 feature registry：
``sma_20, rsi_14, vwm_20, vwm_zscore_60, vol_30, macd``。

依赖：Polars / PyArrow，不依赖 pandas。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import polars as pl

from data_engine import bars_to_polars, polars_to_bars
from data_engine.sources.csv_bars import CsvBarSource
from feature_engine.core import registry as _registry
from feature_engine.core.dag import FeatureDAG
from feature_engine.features import load_all
from feature_engine.storage.metadata import Manifest
from feature_engine.storage.parquet_store import ParquetStore
from feature_engine.streaming.archiver import EodArchiver
from feature_engine.streaming.engine import StreamingEngine, StreamingEngineConfig

# 与写入侧（archiver / test_storage）一致的分区列。
RAW_PARTITION_COLS = ("asset_class", "exchange", "frequency", "trading_date")
FEATURE_PARTITION_COLS = ("feature_group", "frequency", "trading_date")

# 特征数据体 + 原始 OHLCV 骨架列。
_RAW_BODY_COLS = ("symbol", "ts_event", "open", "high", "low", "close", "volume")


# --------------------------------------------------------------------- 读取


def _exchange_from_instrument(instrument_id: str) -> str:
    """从 ``IH2303.CFFEX`` 形式的 instrument_id 中取交易所，取不到则 'unknown'。"""
    if "." in instrument_id:
        return instrument_id.rsplit(".", 1)[1]
    return "unknown"


def _load_market_dataframe(args: argparse.Namespace) -> pl.DataFrame:
    """读取历史行情并归一化为 feature_engine 的列 schema（symbol/ts_event/OHLCV）。

    优先级：``--csv`` > ``--parquet-file`` > ``--market-root``（Hive）。
    """
    if args.csv:
        source = CsvBarSource(
            path=args.csv,
            instrument_id=args.instrument_id,
            warmup_bars=0,
            timestamp_column=args.timestamp_column,
            timestamp_unit=args.timestamp_unit,
        )
        return bars_to_polars(source._bars_cached())

    if args.parquet_file:
        df = pl.read_parquet(args.parquet_file)
        return _ensure_feature_schema(df, args.instrument_id)

    if args.market_root:
        df = _scan_hive_market(args)
        return _ensure_feature_schema(df, args.instrument_id)

    raise SystemExit(
        "必须指定 --market-root 或 --csv 或 --parquet-file 之一作为行情来源"
    )


def _scan_hive_market(args: argparse.Namespace) -> pl.DataFrame:
    """用 pyarrow.dataset 自动推断 Hive 分区读取 market_data，并按可用维度过滤。

    不预设具体分区列：交给 pyarrow 的 hive 推断，再对 schema 中实际存在的
    ``instrument_id`` / ``frequency`` / ``trading_date`` 做等值下推。
    """
    import pyarrow.dataset as ds

    dataset = ds.dataset(args.market_root, format="parquet", partitioning="hive")
    schema_names = set(dataset.schema.names)

    wanted = {
        "instrument_id": args.instrument_id,
        "frequency": args.frequency,
        "trading_date": args.trading_date,
    }
    expr = None
    for col, val in wanted.items():
        if val is not None and col in schema_names:
            cond = ds.field(col) == val
            expr = cond if expr is None else (expr & cond)

    table = dataset.to_table(filter=expr)
    frame = pl.from_arrow(table)
    if isinstance(frame, pl.Series):  # pragma: no cover - defensive
        frame = frame.to_frame()
    return frame


def _ensure_feature_schema(df: pl.DataFrame, instrument_id: str) -> pl.DataFrame:
    """把任意行情 DataFrame 归一化为含 ``symbol`` + ``ts_event`` 的特征输入。

    若已是 feature_engine schema（symbol + ts_event）则原样返回；否则视为
    data_engine 的 BarEvent 形态（instrument_id / event_time_ns），通过 Part 4
    的桥接 ``polars_to_bars -> bars_to_polars`` 归一化。
    """
    cols = set(df.columns)
    if "symbol" in cols and "ts_event" in cols:
        return df
    if "instrument_id" not in cols:
        df = df.with_columns(pl.lit(instrument_id).alias("instrument_id"))
    bars = polars_to_bars(df)
    return bars_to_polars(bars)


# --------------------------------------------------------------------- 计算


def _resolve_feature_names(raw: str) -> list[str]:
    """解析 ``--features`` 逗号列表，校验每个名字都在 registry 中。"""
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        raise SystemExit("--features 不能为空")
    known = set(_registry.registry())
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit(
            f"未知 feature: {unknown}. 当前可用: {sorted(known)}"
        )
    return names


def _compute_features(
    df: pl.DataFrame, feature_names: list[str], *, trading_date: str, frequency: str
) -> tuple[pl.DataFrame, list[str]]:
    """用 FeatureDAG + StreamingEngine 计算特征，返回 (enriched_df, 全部计算的特征名)。

    DAG 会自动把依赖（如 ``vwm_zscore_60`` 依赖 ``vwm_20``）一并拉入，因此
    返回的特征名是拓扑序的全集，便于归档与写 manifest。
    """
    dag = FeatureDAG(feature_names)
    engine = StreamingEngine(
        dag,
        config=StreamingEngineConfig(
            session_id=trading_date,
            frequency=frequency,
            checkpoint_every_n_batches=1_000_000,  # 单批离线计算，无需中途 checkpoint
        ),
    )
    engine.run([df])
    enriched = engine.drain()
    if enriched is None:
        enriched = df
    return enriched, list(dag.order)


# --------------------------------------------------------------------- 落盘


def _write(
    enriched: pl.DataFrame,
    computed_features: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """用 EodArchiver 写 feature_data（可选 raw market_data）+ manifest。"""
    feature_store = ParquetStore(args.feature_root, FEATURE_PARTITION_COLS)
    raw_root = args.raw_root or str(Path(args.feature_root).parent / "market_data")
    raw_store = ParquetStore(raw_root, RAW_PARTITION_COLS)
    manifest = Manifest(args.manifest_root)

    archiver = EodArchiver(
        raw_store=raw_store,
        feature_store=feature_store,
        manifest=manifest,
    )

    partition_values = {
        "asset_class": args.asset_class,
        "exchange": args.exchange or _exchange_from_instrument(args.instrument_id),
        "frequency": args.frequency,
        "trading_date": args.trading_date,
    }
    # 默认不重复写 raw market_data，除非 --write-raw。
    raw_columns = list(_RAW_BODY_COLS) if args.write_raw else []

    return archiver.archive(
        enriched,
        feature_names=computed_features,
        raw_columns=raw_columns,
        partition_values=partition_values,
        mode=args.mode,
    )


# --------------------------------------------------------------------- CLI


def _feature_output_columns(computed_features: list[str], df: pl.DataFrame) -> list[str]:
    """收集本次实际产出的特征列（用于报告）。"""
    out: list[str] = []
    for name in computed_features:
        for col in _registry.get(name).meta.outputs:
            if col in df.columns and col not in out:
                out.append(col)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="历史行情数据 -> 历史特征数据 端到端构建",
    )
    src = p.add_argument_group("行情来源（三选一）")
    src.add_argument("--market-root", help="Hive market_data 根目录")
    src.add_argument("--csv", help="单个 CSV 行情文件")
    src.add_argument("--parquet-file", help="单个 Parquet 行情文件")

    p.add_argument("--feature-root", required=True, help="feature_data 根目录")
    p.add_argument("--manifest-root", required=True, help="manifests 根目录")
    p.add_argument("--trading-date", required=True, help="交易日 YYYY-MM-DD")
    p.add_argument("--instrument-id", required=True, help="标的，如 IH2303.CFFEX")
    p.add_argument("--frequency", default="1m", help="频率，默认 1m")
    p.add_argument(
        "--features",
        required=True,
        help="逗号分隔的 feature 名，如 sma_20,rsi_14,vwm_20",
    )
    p.add_argument(
        "--mode",
        default="overwrite",
        choices=("error", "append", "overwrite"),
        help="写入模式",
    )
    p.add_argument(
        "--write-raw",
        action="store_true",
        help="同时把 raw 行情列写回 market_data（默认不写）",
    )
    p.add_argument("--raw-root", help="raw market_data 根目录（默认 feature_root 同级）")
    p.add_argument("--asset-class", default="unknown", help="资产类别分区值")
    p.add_argument("--exchange", help="交易所分区值（默认从 instrument_id 推断）")
    p.add_argument("--timestamp-column", default="event_time_ns", help="CSV 时间戳列")
    p.add_argument("--timestamp-unit", default="ns", help="CSV 时间戳单位")
    return p


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    load_all()  # 确保内置特征已注册

    feature_names = _resolve_feature_names(args.features)
    market_df = _load_market_dataframe(args)
    if market_df.is_empty():
        raise SystemExit("行情数据为空，没有可计算的行")

    enriched, computed_features = _compute_features(
        market_df,
        feature_names,
        trading_date=args.trading_date,
        frequency=args.frequency,
    )
    feature_cols = _feature_output_columns(computed_features, enriched)
    report = _write(enriched, computed_features, args)

    print("=" * 60)
    print("历史特征数据构建报告")
    print("=" * 60)
    print(f"输入 rows        : {market_df.height}")
    print(f"请求 features    : {feature_names}")
    print(f"实际计算 features: {computed_features}")
    print(f"输出 feature 列  : {feature_cols}")
    print(f"写入 partitions  : {report['partitions_written']}")
    print(f"manifest rows    : {report.get('manifest_rows', 0)}")
    print(f"run_id           : {report['run_id']}")
    print(f"mode             : {report['mode']}")
    print(f"feature_root     : {args.feature_root}")
    print(f"manifest_root    : {args.manifest_root}")
    print("=" * 60)
    return report


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
