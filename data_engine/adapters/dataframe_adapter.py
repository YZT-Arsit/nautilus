"""BarEvent <-> Polars DataFrame 桥接（polars 懒加载）。

``data_engine`` 的标准事件是 :class:`BarEvent`（字段 ``instrument_id`` /
``event_time_ns``）。``feature_engine`` 的批/流式特征路径则以 Polars
``DataFrame`` 为输入，并默认列名 ``symbol`` 和 ``ts_event``。

每个脚本都自己写一遍转换很容易出错，因此把转换集中在这里。

列约定
------
``bars_to_polars`` 产出的 DataFrame 同时保留两套命名，方便下游直接喂给
``feature_engine.streaming.StreamingEngine``，也方便回到 ``BarEvent``：

============== ============================================================
列名           说明
============== ============================================================
``symbol``     = ``BarEvent.instrument_id``（feature_engine 期待的列名）
``instrument_id`` 原始 ``BarEvent.instrument_id``（保留，方便回转）
``ts_event``   由 ``event_time_ns`` 还原的 UTC 纳秒级 ``Datetime``
``event_time_ns`` 原始纳秒整数（保留，回转时优先使用，保证无损）
``open`` ``high`` ``low`` ``close`` ``volume`` OHLCV，原样透传
============== ============================================================

设计取舍：``ts_event`` 是 feature_engine 排序/跨日重置依赖的列，而
``event_time_ns`` 是无损的整数时间戳。两者都保留，``polars_to_bars`` 回转时
优先用 ``event_time_ns``，没有时才从 ``ts_event`` 推导，从而保证
round-trip 不丢精度。

**polars 懒加载**：本模块顶层不 import polars，因此 ``import data_engine``
在没有安装 polars 的纯 Python 环境里也能成功。polars 仅在真正调用
``bars_to_polars`` / ``polars_to_bars`` 时才导入；缺失时给出清晰错误。
不依赖 pandas、不依赖 Nautilus。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from data_engine.events import BarEvent

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    import polars as pl

# feature_engine 侧的标准列名（与 feature_engine.core.types.Cols 对齐）。
_SYMBOL = "symbol"
_TS_EVENT = "ts_event"
_EVENT_TIME_NS = "event_time_ns"
_INSTRUMENT_ID = "instrument_id"

_OHLCV = ("open", "high", "low", "close", "volume")

# bars_to_polars 输出的稳定列顺序。
_OUTPUT_COLUMNS = (
    _SYMBOL,
    _INSTRUMENT_ID,
    _TS_EVENT,
    _EVENT_TIME_NS,
    *_OHLCV,
)


def _require_polars():
    """懒加载 polars；缺失时给出清晰、可操作的错误。"""
    try:
        import polars as pl  # noqa: PLC0415 - 懒加载
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ImportError(
            "bars_to_polars / polars_to_bars 需要 polars。"
            "请 `pip install polars`（仅 feature_engine 的 DataFrame 路径需要它；"
            "data_engine 的核心事件 / CSV / 分钟线路径无需 polars）。"
        ) from exc
    return pl


def _empty_schema(pl) -> dict:
    """空输入时仍返回带正确 schema 的空表，下游 concat/sort 不会缺列报错。"""
    return {
        _SYMBOL: pl.Utf8,
        _INSTRUMENT_ID: pl.Utf8,
        _TS_EVENT: pl.Datetime(time_unit="ns", time_zone="UTC"),
        _EVENT_TIME_NS: pl.Int64,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    }


def bars_to_polars(events: Iterable[BarEvent]) -> "pl.DataFrame":
    """把 ``BarEvent`` 序列转换为 Polars ``DataFrame``。

    输出列见模块 docstring。``instrument_id`` 同时映射为 ``symbol``，
    ``event_time_ns`` 同时还原为 UTC 的 ``ts_event``。
    """
    pl = _require_polars()
    rows = list(events)
    if not rows:
        return pl.DataFrame(schema=_empty_schema(pl))

    df = pl.DataFrame(
        {
            _INSTRUMENT_ID: [e.instrument_id for e in rows],
            _EVENT_TIME_NS: [int(e.event_time_ns) for e in rows],
            "open": [float(e.open) for e in rows],
            "high": [float(e.high) for e in rows],
            "low": [float(e.low) for e in rows],
            "close": [float(e.close) for e in rows],
            "volume": [float(e.volume) for e in rows],
        }
    )
    df = df.with_columns(
        pl.col(_INSTRUMENT_ID).alias(_SYMBOL),
        # 纳秒整数 -> UTC Datetime，作为 feature_engine 的排序/跨日键。
        pl.from_epoch(pl.col(_EVENT_TIME_NS), time_unit="ns")
        .dt.replace_time_zone("UTC")
        .alias(_TS_EVENT),
    )
    return df.select(_OUTPUT_COLUMNS)


def polars_to_bars(
    df: "pl.DataFrame",
    *,
    instrument_id_col: str = "instrument_id",
) -> list[BarEvent]:
    """把 Polars ``DataFrame`` 转换回 ``BarEvent`` 列表。

    instrument_id 解析顺序：``instrument_id_col`` -> ``symbol``。
    时间戳解析顺序：``event_time_ns`` 列（无损）-> 从 ``ts_event`` 推导纳秒。

    缺少 OHLC 时用 ``close`` 兜底，缺少 ``volume`` 时用 ``0.0``，与
    ``data_engine.adapters.make_bar_event`` 的默认行为保持一致。
    """
    pl = _require_polars()  # 缺 polars 时也给出清晰错误（即便 df 已是 polars 对象）
    if df.is_empty():
        return []

    cols = set(df.columns)

    # 解析 instrument_id 列。
    if instrument_id_col in cols:
        id_col = instrument_id_col
    elif _SYMBOL in cols:
        id_col = _SYMBOL
    else:
        raise ValueError(
            f"DataFrame 缺少标的列：既没有 {instrument_id_col!r} 也没有 {_SYMBOL!r}"
        )

    if "close" not in cols:
        raise ValueError("DataFrame 缺少必需的 'close' 列")

    # 统一出一个 event_time_ns 列：优先用已有整数列，否则从 ts_event 推导。
    work = df
    if _EVENT_TIME_NS not in cols:
        if _TS_EVENT not in cols:
            raise ValueError(
                f"DataFrame 缺少时间戳列：既没有 {_EVENT_TIME_NS!r} 也没有 {_TS_EVENT!r}"
            )
        work = work.with_columns(
            pl.col(_TS_EVENT).dt.epoch("ns").alias(_EVENT_TIME_NS)
        )

    records = work.to_dicts()
    out: list[BarEvent] = []
    for row in records:
        close = float(row["close"])

        def _opt(name: str, default: float) -> float:
            val = row.get(name)
            return default if val is None else float(val)

        out.append(
            BarEvent(
                close=close,
                open=_opt("open", close),
                high=_opt("high", close),
                low=_opt("low", close),
                volume=_opt("volume", 0.0),
                instrument_id=str(row[id_col]),
                event_time_ns=int(row[_EVENT_TIME_NS]),
                quote_volume=(
                    None if row.get("quote_volume") is None else float(row["quote_volume"])
                ),
                trade_count=(
                    None if row.get("trade_count") is None else int(row["trade_count"])
                ),
                taker_buy_volume=(
                    None if row.get("taker_buy_volume") is None
                    else float(row["taker_buy_volume"])
                ),
                taker_buy_quote_volume=(
                    None if row.get("taker_buy_quote_volume") is None
                    else float(row["taker_buy_quote_volume"])
                ),
            )
        )
    return out
