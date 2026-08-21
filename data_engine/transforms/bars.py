"""Bar 级工具：频率解析、交易日推导、粗化重采样、合法性校验（纯标准库）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from data_engine.events import BarEvent
from data_engine.time import ONE_SECOND_NS

# market_data 标准 OHLCV 列（落盘 schema 见 HISTORICAL_DATA_LAYOUT.md）。
OHLCV_COLUMNS = (
    "instrument_id",
    "symbol",
    "ts_event",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "trading_date",
    "frequency",
    "volume_is_synthetic",
)

_UNIT_NS = {
    "s": ONE_SECOND_NS,
    "m": 60 * ONE_SECOND_NS,
    "h": 3600 * ONE_SECOND_NS,
    "d": 86400 * ONE_SECOND_NS,
}


def parse_frequency(frequency: str) -> int:
    """把 ``"1m"`` / ``"5m"`` / ``"15m"`` / ``"1s"`` / ``"1h"`` 解析成纳秒间隔。"""
    f = str(frequency).strip().lower()
    if not f or not f[-1].isalpha():
        raise ValueError(f"无法解析的 frequency {frequency!r}（示例：1m, 5m, 15m, 1s, 1h）")
    unit = f[-1]
    if unit not in _UNIT_NS:
        raise ValueError(f"不支持的 frequency 单位 {unit!r}（支持 {sorted(_UNIT_NS)}）")
    num_part = f[:-1]
    if not num_part:
        raise ValueError(f"frequency 必须显式包含正整数：{frequency!r}")
    try:
        n = int(num_part)
    except ValueError as exc:
        raise ValueError(f"无法解析的 frequency 数值 {num_part!r}（来自 {frequency!r}）") from exc
    if n <= 0:
        raise ValueError(f"frequency 必须为正：{frequency!r}")
    return n * _UNIT_NS[unit]


def derive_trading_date(event_time_ns: int) -> str:
    """从纳秒时间戳推导交易日 ``YYYY-MM-DD``（UTC 日期）。

    注意：这是简化实现，使用 UTC 日历日。期货夜盘的交易日归属（夜盘计入
    次一交易日）尚未实现，见 ``docs/HISTORICAL_DATA_LAYOUT.md`` 的 backlog；
    需要精确夜盘归属时，请在 build 时显式传入 ``trading_date``。
    """
    dt = datetime.fromtimestamp(int(event_time_ns) / 1_000_000_000, tz=timezone.utc)
    return dt.date().isoformat()


def validate_bars(bars: Iterable[BarEvent]) -> list[str]:
    """检查 OHLC 合法性、重复时间戳、时间单调性。返回问题描述列表（空=通过）。"""
    issues: list[str] = []
    last_ts: dict[str, int] = {}
    seen: dict[str, set[int]] = {}
    for b in bars:
        # OHLC 合法性：low <= open/close <= high 且 low <= high。
        if not (b.low <= b.high and b.low <= b.open <= b.high and b.low <= b.close <= b.high):
            issues.append(
                f"非法 OHLC @ {b.instrument_id} t={b.event_time_ns}: "
                f"o={b.open} h={b.high} l={b.low} c={b.close}"
            )
        inst = b.instrument_id
        # 重复时间戳（同一标的、同一桶出现多根 bar）。
        s = seen.setdefault(inst, set())
        if b.event_time_ns in s:
            issues.append(f"重复时间戳 @ {inst} t={b.event_time_ns}")
        s.add(b.event_time_ns)
        # 时间单调性（同一标的内）。
        prev = last_ts.get(inst)
        if prev is not None and b.event_time_ns < prev:
            issues.append(
                f"时间非单调 @ {inst}: {b.event_time_ns} < 前一根 {prev}"
            )
        last_ts[inst] = b.event_time_ns
    return issues


def resample_bars(bars: Iterable[BarEvent], frequency: str) -> list[BarEvent]:
    """把细粒度 bar 粗化重采样到 ``frequency``（OHLCV 聚合，桶左沿标签）。

    适合 1m -> 5m / 15m。按 (instrument, 桶左沿) 聚合：open=桶内首根 open，
    high=max，low=min，close=桶内末根 close，volume=求和。
    """
    interval = parse_frequency(frequency)
    rows = sorted(bars, key=lambda b: (b.instrument_id, b.event_time_ns))

    buckets: dict[tuple[str, int], BarEvent] = {}
    order: list[tuple[str, int]] = []
    for b in rows:
        start = (b.event_time_ns // interval) * interval
        key = (b.instrument_id, start)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = BarEvent(
                close=b.close,
                open=b.open,
                high=b.high,
                low=b.low,
                volume=b.volume,
                instrument_id=b.instrument_id,
                event_time_ns=start,
                quote_volume=b.quote_volume,
                trade_count=b.trade_count,
                taker_buy_volume=b.taker_buy_volume,
                taker_buy_quote_volume=b.taker_buy_quote_volume,
            )
            order.append(key)
        else:
            cur.high = max(cur.high, b.high)
            cur.low = min(cur.low, b.low)
            cur.close = b.close  # rows 已按时间排序，最后一根即收盘
            cur.volume += b.volume
            if b.quote_volume is not None:
                cur.quote_volume = (cur.quote_volume or 0.0) + b.quote_volume
            if b.trade_count is not None:
                cur.trade_count = (cur.trade_count or 0) + b.trade_count
            if b.taker_buy_volume is not None:
                cur.taker_buy_volume = (cur.taker_buy_volume or 0.0) + b.taker_buy_volume
            if b.taker_buy_quote_volume is not None:
                cur.taker_buy_quote_volume = (
                    (cur.taker_buy_quote_volume or 0.0) + b.taker_buy_quote_volume
                )
    order.sort()
    return [buckets[k] for k in order]
