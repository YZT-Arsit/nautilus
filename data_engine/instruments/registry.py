"""合约信息的静态 provider 与（可选）Parquet 落盘。

* :class:`StaticInstrumentProvider` —— 用一组现成的 :class:`InstrumentInfo`
  当作 provider，测试和离线场景不依赖任何网络。
* :func:`instruments_to_polars` / :func:`write_instruments_parquet` —— 把合约
  信息落成 Hive-style Parquet，纳入历史数据体系，按 ``exchange`` /
  ``as_of_date`` 分区复用。

polars / pyarrow 都是**懒加载**：只构造/传递 ``InstrumentInfo`` 时无需安装它们。
不使用 pandas。
"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from data_engine.instruments.models import InstrumentInfo

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    import polars as pl


@runtime_checkable
class InstrumentProvider(Protocol):
    """合约信息 provider 协议：CCXT / 静态 / 未来的 CTP 都满足它。"""

    def load_instruments(self) -> list[InstrumentInfo]: ...


class StaticInstrumentProvider:
    """用预置 ``InstrumentInfo`` 列表充当 provider（测试 / 离线用）。"""

    def __init__(self, instruments: list[InstrumentInfo]) -> None:
        self._instruments = list(instruments)

    def load_instruments(self) -> list[InstrumentInfo]:
        return list(self._instruments)


# 落盘时的标量列顺序（raw 单独序列化为 raw_json）。
_SCALAR_FIELDS = tuple(f.name for f in fields(InstrumentInfo) if f.name != "raw")

# 合约信息历史落盘的 Hive 分区列。
INSTRUMENT_PARTITION_COLS = ("exchange", "as_of_date")


def instruments_to_polars(instruments: list[InstrumentInfo]) -> "pl.DataFrame":
    """把 ``InstrumentInfo`` 列表转成 Polars ``DataFrame``。

    ``raw`` 字典序列化为 ``raw_json`` 字符串列，保证 schema 稳定、可落 Parquet。
    """
    import polars as pl  # noqa: PLC0415 - 懒加载

    data: dict[str, list[object]] = {name: [] for name in _SCALAR_FIELDS}
    data["raw_json"] = []
    for inst in instruments:
        for name in _SCALAR_FIELDS:
            data[name].append(getattr(inst, name))
        data["raw_json"].append(json.dumps(inst.raw, sort_keys=True, default=str))

    if not instruments:
        # 空输入也返回带列名的空表，避免下游 KeyError。
        return pl.DataFrame({k: [] for k in data})
    return pl.DataFrame(data)


def write_instruments_parquet(
    instruments: list[InstrumentInfo],
    root: str | Path,
    *,
    exchange: str,
    as_of_date: str,
) -> list[Path]:
    """把合约信息写成 Hive-style Parquet，返回写出的文件路径列表。

    布局::

        {root}/exchange=<exchange>/as_of_date=<as_of_date>/part-*.parquet
    """
    import pyarrow.dataset as ds  # noqa: PLC0415 - 懒加载

    df = instruments_to_polars(instruments)
    df = df.with_columns(
        pl_lit(exchange).alias("exchange"),
        pl_lit(as_of_date).alias("as_of_date"),
    )
    table = df.to_arrow()

    written: list[Path] = []

    def _visit(file: ds.WrittenFile) -> None:
        written.append(Path(file.path))

    ds.write_dataset(
        table,
        base_dir=str(root),
        format="parquet",
        partitioning=list(INSTRUMENT_PARTITION_COLS),
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
        basename_template="part-{i}.parquet",
        file_visitor=_visit,
    )
    return written


def pl_lit(value: object):
    """``pl.lit`` 的懒加载小封装，避免模块顶层 import polars。"""
    import polars as pl  # noqa: PLC0415

    return pl.lit(value)
