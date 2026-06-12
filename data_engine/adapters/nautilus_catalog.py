"""可选 adapter：从 Nautilus Trader 的 ParquetDataCatalog 读取 QuoteTick。

**边界约定**：这是 *唯一* 允许 import Nautilus 的 data_engine 文件，且它**不**
被 ``data_engine`` / ``data_engine.adapters`` 的 ``__init__`` 导入——core 完全
不依赖它。Nautilus 是一个**可选数据源**：没装 nautilus_trader 时，本模块导入
仍然成功（顶层不 import nautilus），只有真正调用时才懒加载，并在缺失时给出
清晰错误。

职责仅限“接入”：把 Nautilus 的 ``QuoteTick`` 归一化成 data_engine 的中性
tick（dict），再交给 :func:`data_engine.transforms.aggregate_ticks_to_bars`
聚合成标准分钟线。**不**把 Nautilus 对象泄漏到 core / feature_engine。
"""
from __future__ import annotations

from typing import Any, Iterator

from data_engine.transforms.tick_to_bar import MinuteBarResult, aggregate_ticks_to_bars


def _require_nautilus():
    """懒加载 nautilus_trader catalog；缺失时给出清晰、可操作的错误。"""
    try:
        from nautilus_trader.persistence.catalog import ParquetDataCatalog  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ImportError(
            "nautilus_catalog adapter 需要安装并编译 nautilus_trader。"
            "Nautilus 在本项目中是*可选*数据源；自研 data_engine 的 CSV / Parquet / "
            "分钟线路径都不需要它。"
        ) from exc
    return ParquetDataCatalog


def iter_quote_ticks_as_dicts(
    catalog_path: str,
    *,
    instrument_id: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Iterator[dict]:
    """从 Nautilus catalog 读取 QuoteTick，懒加载、逐条 yield 中性 tick dict。

    归一化后的字段：``instrument_id`` / ``event_time_ns`` / ``bid`` / ``ask``
    （价格走 ``transforms`` 的 ``(bid+ask)/2`` 兜底）/ ``size``（取 bid_size+
    ask_size 之和，若无则缺省，由聚合器判定为合成量）。
    """
    ParquetDataCatalog = _require_nautilus()
    catalog = ParquetDataCatalog(catalog_path)
    kwargs: dict[str, Any] = {}
    if instrument_id is not None:
        kwargs["instrument_ids"] = [instrument_id]
    if start is not None:
        kwargs["start"] = start
    if end is not None:
        kwargs["end"] = end

    for tick in catalog.quote_ticks(**kwargs):
        bid = getattr(tick, "bid_price", None)
        ask = getattr(tick, "ask_price", None)
        bid_size = getattr(tick, "bid_size", None)
        ask_size = getattr(tick, "ask_size", None)
        size = None
        if bid_size is not None or ask_size is not None:
            size = float(bid_size or 0.0) + float(ask_size or 0.0)
        yield {
            "instrument_id": str(getattr(tick, "instrument_id", instrument_id)),
            "event_time_ns": int(getattr(tick, "ts_event", 0)),
            "bid": None if bid is None else float(bid),
            "ask": None if ask is None else float(ask),
            "size": size,
        }


def build_bars_from_catalog(
    catalog_path: str,
    *,
    instrument_id: str | None = None,
    frequency: str = "1m",
    start: Any = None,
    end: Any = None,
    trading_date: str | None = None,
) -> MinuteBarResult:
    """从 Nautilus catalog 的 QuoteTick 直接构建标准分钟线（经中性 tick）。"""
    ticks = iter_quote_ticks_as_dicts(
        catalog_path, instrument_id=instrument_id, start=start, end=end
    )
    return aggregate_ticks_to_bars(
        ticks,
        frequency=frequency,
        default_instrument=instrument_id,
        trading_date=trading_date,
    )


__all__ = ["iter_quote_ticks_as_dicts", "build_bars_from_catalog"]
