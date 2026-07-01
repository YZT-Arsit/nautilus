"""分钟线构建服务：tick/quote/bar -> 标准 OHLCV 分钟线 -> market_data 落盘。

聚合逻辑全部复用 :mod:`data_engine.transforms`（纯标准库，可独立测试），本服务
只负责编排和（可选的）Hive Parquet 落盘。落盘用 pyarrow，**懒加载**——只做
聚合（``build_*``）时不需要 pyarrow。

market_data 分区见 :data:`feature_engine.storage.layout.MARKET_DATA_PARTITION_COLS`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from data_engine.transforms import aggregate_ticks_to_bars
from data_engine.transforms.tick_to_bar import MinuteBarResult

# 落盘的列 = 数据列 + 分区列（分区列由路径承载，pyarrow 从 body 移除）。
# 分区列须与 MARKET_DATA_PARTITION_COLS 对齐：
#   asset_class, exchange, venue_type, symbol, data_type, freq, date
_MARKET_COLUMNS = (
    "instrument_id",
    "symbol",
    "ts_event",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "volume_is_synthetic",
    # partition columns
    "asset_class",
    "exchange",
    "venue_type",
    "data_type",
    "freq",
    "date",
)


class MinuteBarBuilder:
    """把 tick/quote/bar 聚合成分钟线并（可选）落入 market_data。"""

    def __init__(
        self,
        *,
        asset_class: str = "future",
        exchange: str = "UNKNOWN",
        venue_type: str = "unknown",
    ) -> None:
        self.asset_class = asset_class
        self.exchange = exchange
        self.venue_type = venue_type

    # ---------------------------------------------------------------- build

    def build_from_ticks(
        self,
        ticks: Iterable[Any],
        *,
        instrument_id: str,
        frequency: str = "1m",
        trading_date: str | None = None,
        price_field: str | None = None,
        size_field: str | None = None,
    ) -> MinuteBarResult:
        """纯聚合，不落盘。返回 :class:`MinuteBarResult`（含 bars / rows / 校验）。"""
        return aggregate_ticks_to_bars(
            ticks,
            frequency=frequency,
            default_instrument=instrument_id,
            price_field=price_field,
            size_field=size_field,
            trading_date=trading_date,
        )

    # ----------------------------------------------------------------- write

    def write_market_data(
        self,
        result: MinuteBarResult,
        *,
        market_root: str | Path,
        strict: bool = True,
    ) -> list[Path]:
        """把分钟线写成 Hive Parquet（pyarrow 懒加载）。返回写出的文件路径。

        ``strict=True`` 时，若 :attr:`MinuteBarResult.issues` 非空（OHLC 非法 /
        重复时间戳 / 时间非单调），拒绝写入并抛错。
        """
        if strict and result.issues:
            raise ValueError(
                f"分钟线校验未通过，拒绝落盘：{result.issues[:5]}"
                + (" ..." if len(result.issues) > 5 else "")
            )
        if not result.rows:
            return []

        try:
            import pyarrow as pa  # noqa: PLC0415
            import pyarrow.dataset as ds  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise ImportError(
                "write_market_data 需要 pyarrow（仅落盘路径需要；build_* 聚合不需要）。"
            ) from exc
        # 懒加载分区列：让纯聚合路径（build_*）无需触达 storage（polars）。
        from feature_engine.storage.layout import MARKET_DATA_PARTITION_COLS  # noqa: PLC0415

        # 补上分区列：asset_class / exchange / venue_type / data_type，并把
        # tick_to_bar 的 trading_date / frequency 映射到锁定布局的 date / freq。
        enriched = [
            {
                **row,
                "asset_class": self.asset_class,
                "exchange": self.exchange,
                "venue_type": self.venue_type,
                "data_type": "bar",
                "freq": row.get("frequency"),
                "date": row.get("trading_date"),
            }
            for row in result.rows
        ]
        table = pa.Table.from_pylist([
            {k: row.get(k) for k in _MARKET_COLUMNS} for row in enriched
        ])

        written: list[Path] = []

        def _visit(f: "ds.WrittenFile") -> None:
            written.append(Path(f.path))

        ds.write_dataset(
            table,
            base_dir=str(market_root),
            format="parquet",
            partitioning=list(MARKET_DATA_PARTITION_COLS),
            partitioning_flavor="hive",
            existing_data_behavior="overwrite_or_ignore",
            basename_template="part-{i}.parquet",
            file_visitor=_visit,
        )
        return written

    # -------------------------------------------------------------- convenience

    def build_and_write(
        self,
        ticks: Iterable[Any],
        *,
        instrument_id: str,
        market_root: str | Path,
        frequency: str = "1m",
        trading_date: str | None = None,
        price_field: str | None = None,
        size_field: str | None = None,
        strict: bool = True,
    ) -> tuple[MinuteBarResult, list[Path]]:
        result = self.build_from_ticks(
            ticks,
            instrument_id=instrument_id,
            frequency=frequency,
            trading_date=trading_date,
            price_field=price_field,
            size_field=size_field,
        )
        paths = self.write_market_data(result, market_root=market_root, strict=strict)
        return result, paths


__all__ = ["MinuteBarBuilder"]
