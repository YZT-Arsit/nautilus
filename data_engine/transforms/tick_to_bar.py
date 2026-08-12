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
* 输入允许乱序：按 (instrument, ts, trade_id) 稳定排序后聚合。
* ``quote_volume`` 优先累加事件原始 ``quote_quantity``；只有字段缺失时才
  显式回退到 ``price * quantity``，并记录 fallback count。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any
from typing import Iterable

from data_engine.events import BarEvent
from data_engine.transforms.bars import OHLCV_COLUMNS
from data_engine.transforms.bars import derive_trading_date
from data_engine.transforms.bars import parse_frequency
from data_engine.transforms.bars import validate_bars


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
        "事件缺少价格：尝试过 price/last/close/(bid,ask)，都没有。可用 price_field 显式指定。"
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
    quote_quantity_fallback_count: int = 0
    issues: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.bars)


class _Bucket:
    __slots__ = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "_volume_c",
        "turnover",
        "_turnover_c",
        "quote_volume",
        "_quote_volume_c",
        "taker_buy_volume",
        "_taker_buy_volume_c",
        "taker_buy_quote_volume",
        "_taker_buy_quote_volume_c",
        "n",
    )

    def __init__(self, price: float, size: float, quote_quantity: float, is_buy: bool) -> None:
        self.open = self.high = self.low = self.close = price
        self.volume = size
        self._volume_c = 0.0
        self.turnover = price * size
        self._turnover_c = 0.0
        self.quote_volume = quote_quantity
        self._quote_volume_c = 0.0
        self.taker_buy_volume = size if is_buy else 0.0
        self._taker_buy_volume_c = 0.0
        self.taker_buy_quote_volume = quote_quantity if is_buy else 0.0
        self._taker_buy_quote_volume_c = 0.0
        self.n = 1

    def _add(self, field: str, compensation_field: str, value: float) -> None:
        """Neumaier compensated addition without retaining every trade value."""
        current = float(getattr(self, field))
        total = current + value
        correction = float(getattr(self, compensation_field))
        if abs(current) >= abs(value):
            correction += (current - total) + value
        else:
            correction += (value - total) + current
        setattr(self, field, total)
        setattr(self, compensation_field, correction)

    def update(self, price: float, size: float, quote_quantity: float, is_buy: bool) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self._add("volume", "_volume_c", size)
        self._add("turnover", "_turnover_c", price * size)
        self._add("quote_volume", "_quote_volume_c", quote_quantity)
        if is_buy:
            self._add("taker_buy_volume", "_taker_buy_volume_c", size)
            self._add(
                "taker_buy_quote_volume",
                "_taker_buy_quote_volume_c",
                quote_quantity,
            )
        self.n += 1

    def total(self, field: str) -> float:
        return float(getattr(self, field)) + float(getattr(self, f"_{field}_c"))


def _trade_id_key(value: Any, input_order: int) -> tuple[int, object, int]:
    if value is None:
        return (2, "", input_order)
    try:
        return (0, int(value), input_order)
    except (TypeError, ValueError):
        return (1, str(value), input_order)


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
    extracted: list[
        tuple[str, int, tuple[int, object, int], float, float | None, float | None, bool]
    ] = []
    any_real_size = False
    quote_quantity_fallback_count = 0
    for input_order, ev in enumerate(ticks):
        inst = _get(ev, "instrument_id") or default_instrument
        if inst is None:
            raise ValueError("事件缺少 instrument_id，且未提供 default_instrument")
        ts = _ts_ns(ev)
        price = _price(ev, price_field)
        size = _size(ev, size_field)
        if size is not None:
            any_real_size = True
        quote_quantity_raw = _get(ev, "quote_quantity")
        quote_quantity = None if quote_quantity_raw is None else float(quote_quantity_raw)
        quote_quantity_source = _get(ev, "quote_quantity_source")
        if quote_quantity is None or quote_quantity_source == "price_x_quantity_fallback":
            quote_quantity_fallback_count += 1
        side = _get(ev, "side")
        is_buyer_maker = _get(ev, "is_buyer_maker")
        is_buy = str(side).upper() == "BUY" if side is not None else is_buyer_maker is False
        extracted.append(
            (
                str(inst),
                ts,
                _trade_id_key(_get(ev, "trade_id"), input_order),
                price,
                size,
                quote_quantity,
                is_buy,
            )
        )

    volume_is_synthetic = not any_real_size
    extracted.sort(key=lambda row: (row[0], row[1], row[2]))

    # 2) 分桶聚合（桶左沿对齐 epoch）。
    buckets: dict[tuple[str, int], _Bucket] = {}
    order: list[tuple[str, int]] = []
    for inst, ts, _trade_key, price, size, quote_quantity, is_buy in extracted:
        bucket_start = (ts // interval) * interval
        size_val = 1.0 if size is None else size  # 无真实 size -> 计数
        quote_value = price * size_val if quote_quantity is None else quote_quantity
        key = (inst, bucket_start)
        b = buckets.get(key)
        if b is None:
            buckets[key] = _Bucket(price, size_val, quote_value, is_buy)
            order.append(key)
        else:
            b.update(price, size_val, quote_value, is_buy)

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
                volume=b.total("volume"),
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
                "volume": b.total("volume"),
                "turnover": b.total("turnover"),
                "quote_volume": b.total("quote_volume"),
                "trade_count": b.n,
                "taker_buy_volume": b.total("taker_buy_volume"),
                "taker_buy_quote_volume": b.total("taker_buy_quote_volume"),
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
        quote_quantity_fallback_count=quote_quantity_fallback_count,
        issues=issues,
    )


# 重新导出，方便 ``from data_engine.transforms.tick_to_bar import OHLCV_COLUMNS``。
__all__ = ["aggregate_ticks_to_bars", "MinuteBarResult", "OHLCV_COLUMNS"]
