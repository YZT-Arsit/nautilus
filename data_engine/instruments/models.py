"""合约/标的元数据模型。

:class:`InstrumentInfo` 是一个零依赖（只用标准库）的不可变 dataclass，描述一个
可交易合约/标的的静态元数据：精度、最小下单量、合约乘数、到期、是否活跃等。

它是数据接入层的一部分：合约信息也属于历史数据体系，可按 ``exchange`` /
``as_of_date`` 落盘复用（见 :mod:`data_engine.instruments.registry`）。

刻意保持零依赖：不 import polars / pyarrow / ccxt / nautilus，任何环境都能
构造和传递 ``InstrumentInfo``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InstrumentInfo:
    """单个合约/标的的静态元数据。

    字段语义贴近 CCXT 的 market dict，但保持与具体来源解耦，方便国内期货 /
    柜台 provider 复用同一模型。
    """

    instrument_id: str
    exchange: str
    symbol: str
    market_type: str  # spot / swap / future / option / ...
    base: str | None = None
    quote: str | None = None
    settle: str | None = None
    contract_size: float | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    price_tick: float | None = None
    amount_step: float | None = None
    min_amount: float | None = None
    min_notional: float | None = None
    expiry: int | None = None  # 毫秒时间戳；永续/现货为 None
    active: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)
