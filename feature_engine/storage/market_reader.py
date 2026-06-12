"""历史行情数据查询接口（MarketDataReader）。

与 :class:`feature_engine.storage.feature_reader.FeatureDataReader` 对称：前者读
``feature_data``，本类读 ``market_data``。两者在历史数据体系里**平级**
（见 ``docs/HISTORICAL_DATA_LAYOUT.md``）。

market_data 的 Hive 分区列见
:data:`feature_engine.storage.layout.MARKET_DATA_PARTITION_COLS`：
``asset_class / exchange / frequency / trading_date / instrument_id``。

pyarrow / polars **懒加载**：``import`` 本模块不需要它们；只有真正查询时才导入，
缺失时给出清晰错误。不依赖 pandas、不依赖 Nautilus。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from feature_engine.storage.layout import MARKET_DATA_PARTITION_COLS

if TYPE_CHECKING:  # pragma: no cover
    import polars as pl

    from data_engine.events import BarEvent


def _require(mod: str):
    try:
        return __import__(mod)
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ImportError(
            f"MarketDataReader 需要 {mod}。请安装后再查询历史行情；"
            "（仅查询路径需要，构造 reader 本身零重依赖）。"
        ) from exc


class MarketDataReader:
    """只读查询 market_data Hive Parquet 数据集。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def scan(
        self,
        *,
        asset_class: str | None = None,
        exchange: str | None = None,
        frequency: str | None = None,
        trading_date: str | list[str] | None = None,
        instrument_id: str | None = None,
        columns: list[str] | None = None,
    ) -> "pl.DataFrame":
        """按分区裁剪读取行情，返回 Polars ``DataFrame``。"""
        _require("pyarrow")
        _require("polars")
        import pyarrow.dataset as _ds  # noqa: PLC0415
        import polars as pl  # noqa: PLC0415

        dataset = _ds.dataset(
            str(self.root), format="parquet", partitioning="hive"
        )
        filt = self._build_filter(
            _ds,
            asset_class=asset_class,
            exchange=exchange,
            frequency=frequency,
            trading_date=trading_date,
            instrument_id=instrument_id,
        )
        table = dataset.to_table(filter=filt, columns=columns)
        df = pl.from_arrow(table)
        # 分区列在 hive 数据集里通过路径恢复；确保 symbol/instrument_id 可用。
        if "symbol" not in df.columns and "instrument_id" in df.columns:
            df = df.with_columns(pl.col("instrument_id").alias("symbol"))
        return df

    def read_bars(self, **kwargs: Any) -> list["BarEvent"]:
        """同 :meth:`scan`，但回到 ``BarEvent`` 列表（经 polars_to_bars）。"""
        from data_engine.adapters.dataframe_adapter import polars_to_bars  # noqa: PLC0415

        df = self.scan(**kwargs)
        return polars_to_bars(df)

    @staticmethod
    def _build_filter(
        _ds,
        *,
        asset_class: str | None,
        exchange: str | None,
        frequency: str | None,
        trading_date: str | list[str] | None,
        instrument_id: str | None,
    ):
        conds = []
        eq = {
            "asset_class": asset_class,
            "exchange": exchange,
            "frequency": frequency,
            "instrument_id": instrument_id,
        }
        for col in MARKET_DATA_PARTITION_COLS:
            val = eq.get(col)
            if val is not None:
                conds.append(_ds.field(col) == val)
        if trading_date is not None:
            if isinstance(trading_date, (list, tuple, set)):
                conds.append(_ds.field("trading_date").isin(list(trading_date)))
            else:
                conds.append(_ds.field("trading_date") == trading_date)
        if not conds:
            return None
        expr = conds[0]
        for c in conds[1:]:
            expr = expr & c
        return expr


__all__ = ["MarketDataReader"]
