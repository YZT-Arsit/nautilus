"""把 tick / quote-tick / bar 聚合成标准 OHLCV 分钟线（纯标准库）。

输入是“鸭子类型”的事件序列：dict 或对象都行。提取规则：

* ``instrument_id``  —— ``instrument_id``（缺失用 ``default_instrument``）。
* 时间戳            —— ``event_time_ns``（纳秒整数，首选）或 ``ts_event``
  （整数纳秒或 ``datetime``）。
* 价格              —— ``price_field`` 指定列；否则依次尝试
  ``price`` -> ``last`` -> ``close`` -> ``(bid+ask)/2``。
* 成交量            —— ``size_field`` 指定列；否则依次尝试
  ``size`` -> ``volume`` -> ``quantity`` -> ``qty``。**全部缺失时**，成交量
  退化为“tick 计数”，并在结果里把 ``volume_is_synthetic`` 标记为 ``True``，
  绝不冒充真实成交量。

输出：:class:`MinuteBarResult`，含标准 :class:`BarEvent` 列表与可直接落盘的
OHLCV 行（dict，列见 ``OHLCV_COLUMNS``）。

约定
----
* bar 以**桶左沿**（bucket start）作为 ``event_time_ns`` 标签，``label="left"``。
* OHLC 由构造保证 ``low <= open/close <= high``，并再做一次显式校验。
* 输入允许乱序：按 (instrument, ts) 排序后聚合。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from data_engine.events import BarEvent
from data_engine.transforms.bars import (
    OHLCV_COLUMNS,
    derive_trading_date,
    parse_frequency,
    validate_bars,
)

_PRICE_FALLBACKS = ("price", "last", "close")
_SIZE_FALLBACKS = ("size", "volume", "quantity", "qty")


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _ts_ns(obj: Any) -> int:
    ts = _get(obj, "event_time_ns")
    if ts is None:
        ts = _get(obj, "ts_event")
    if ts is None:
        raise ValueError("事件缺少时间戳：需要 'event_time_ns' 或 'ts_event'")
    if isinstance(ts, datetime):
        return int(ts.timestamp() * 1_000_000_000)
    return int(ts)


def _price(obj: Any, price_field: str | None) -> float:
    if price_field is not None:
        val = _get(obj, price_field)
        if val is None:
            raise ValueError(f"事件缺少价格列 {price_field!r}")
        return float(val)
    for name in _PRICE_FALLBACKS:
        val = _get(obj, name)
        if val is not None:
            return float(val)
    bid, ask = _get(obj, "bid"), _get(obj, "ask")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2.0
    raise ValueError(
        "事件缺少价格：尝试过 price/last/close/(bid,ask)，都没有。"
        "可用 price_field 显式指定。"
    )


def _size(obj: Any, size_field: str | None) -> float | None:
    if size_field is not None:
        val = _get(obj, size_field)
        return None if val is None else float(val)
    for name in _SIZE_FALLBACKS:
        val = _get(obj, name)
        if val is not None:
            return float(val)
    return None


@dataclass
class MinuteBarResult:
    """分钟线聚合结果。"""

    bars: list[BarEvent]
    rows: list[dict]  # 可直接落盘的 OHLCV 行（列见 OHLCV_COLUMNS）
    frequency: str
    volume_is_synthetic: bool
    issues: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.bars)


class _Bucket:
    __slots__ = ("open", "high", "low", "close", "volume", "turnover", "n")

    def __init__(self, price: float, size: float) -> None:
        self.open = self.high = self.low = self.close = price
        self.volume = size
        self.turnover = price * size
        self.n = 1

    def update(self, price: float, size: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size
        self.turnover += price * size
        self.n += 1


def aggregate_ticks_to_bars(
    ticks: Iterable[Any],
    *,
    frequency: str = "1m",
    default_instrument: str | None = None,
    price_field: str | None = None,
    size_field: str | None = None,
    trading_date: str | None = None,
) -> MinuteBarResult:
    """把 tick/quote/bar 序列聚合为 ``frequency`` 分钟线。

    Parameters
    ----------
    frequency:
        ``1m`` / ``5m`` / ``15m`` / ``1s`` / ``1h`` ... 见 :func:`parse_frequency`。
    default_instrument:
        事件不带 ``instrument_id`` 时使用的默认标的。
    trading_date:
        覆盖每根 bar 的交易日；缺省按 bar 时间戳的 UTC 日期推导（夜盘归属见
        ``HISTORICAL_DATA_LAYOUT.md`` backlog）。
    """
    interval = parse_frequency(frequency)

    # 1) 提取并按 (instrument, ts) 排序，允许乱序输入。
    extracted: list[tuple[str, int, float, float | None]] = []
    any_real_size = False
    for ev in ticks:
        inst = _get(ev, "instrument_id") or default_instrument
        if inst is None:
            raise ValueError(
                "事件缺少 instrument_id，且未提供 default_instrument"
            )
        ts = _ts_ns(ev)
        price = _price(ev, price_field)
        size = _size(ev, size_field)
        if size is not None:
            any_real_size = True
        extracted.append((str(inst), ts, price, size))

    volume_is_synthetic = not any_real_size
    extracted.sort(key=lambda r: (r[0], r[1]))

    # 2) 分桶聚合（桶左沿对齐 epoch）。
    buckets: dict[tuple[str, int], _Bucket] = {}
    order: list[tuple[str, int]] = []
    for inst, ts, price, size in extracted:
        bucket_start = (ts // interval) * interval
        size_val = 1.0 if size is None else size  # 无真实 size -> 计数
        key = (inst, bucket_start)
        b = buckets.get(key)
        if b is None:
            buckets[key] = _Bucket(price, size_val)
            order.append(key)
        else:
            b.update(price, size_val)

    # 3) 物化成 BarEvent + OHLCV 行（按 instrument, ts 稳定排序）。
    order.sort()
    bars: list[BarEvent] = []
    rows: list[dict] = []
    for inst, bucket_start in order:
        b = buckets[(inst, bucket_start)]
        bars.append(
            BarEvent(
                close=b.close,
                open=b.open,
                high=b.high,
                low=b.low,
                volume=b.volume,
                instrument_id=inst,
                event_time_ns=bucket_start,
            )
        )
        td = trading_date or derive_trading_date(bucket_start)
        rows.append(
            {
                "instrument_id": inst,
                "symbol": inst,
                "ts_event": bucket_start,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "turnover": b.turnover,
                "trading_date": td,
                "frequency": frequency,
                "volume_is_synthetic": volume_is_synthetic,
            }
        )

    issues = validate_bars(bars)
    return MinuteBarResult(
        bars=bars,
        rows=rows,
        frequency=frequency,
        volume_is_synthetic=volume_is_synthetic,
        issues=issues,
    )


# 重新导出，方便 ``from data_engine.transforms.tick_to_bar import OHLCV_COLUMNS``。
__all__ = ["aggregate_ticks_to_bars", "MinuteBarResult", "OHLCV_COLUMNS"]
