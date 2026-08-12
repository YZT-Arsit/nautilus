"""Resample non-empty StandardBar rows onto a coarser aligned clock."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any
from typing import Iterable

from data_engine.events import BarEvent
from data_engine.transforms.bars import derive_trading_date
from data_engine.transforms.bars import parse_frequency
from data_engine.transforms.bars import validate_bars
from data_engine.transforms.tick_to_bar import MinuteBarResult
from data_engine.transforms.tick_to_bar import _get
from data_engine.transforms.tick_to_bar import _ts_ns


_SUM_FIELDS = (
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
)


def _required_number(value: Any, field: str) -> float:
    if value is None:
        raise ValueError(f"bar is missing required field {field!r}")
    return float(value)


def resample_standard_bars(
    child_bars: Iterable[Any],
    *,
    frequency: str,
    default_instrument: str | None = None,
    trading_date: str | None = None,
) -> MinuteBarResult:
    """Aggregate existing non-empty bars without manufacturing missing children.

    Input bars may be dicts or objects. Each output bucket uses the first child
    open, extrema across child highs/lows, and the last child close. Flow fields
    are summed. Boundaries are epoch-aligned, matching tick aggregation.
    """
    interval_ns = parse_frequency(frequency)
    grouped: dict[tuple[str, int], list[tuple[int, int, Any]]] = defaultdict(list)
    for input_order, bar in enumerate(child_bars):
        instrument = _get(bar, "instrument_id") or default_instrument
        if instrument is None:
            raise ValueError("bar is missing instrument_id and no default was supplied")
        ts_ns = _ts_ns(bar)
        bucket_ns = ts_ns // interval_ns * interval_ns
        grouped[(str(instrument), bucket_ns)].append((ts_ns, input_order, bar))

    bars: list[BarEvent] = []
    rows: list[dict[str, Any]] = []
    for instrument, bucket_ns in sorted(grouped):
        children = sorted(grouped[(instrument, bucket_ns)], key=lambda item: (item[0], item[1]))
        first = children[0][2]
        last = children[-1][2]
        open_price = _required_number(_get(first, "open"), "open")
        close_price = _required_number(_get(last, "close"), "close")
        high_price = max(_required_number(_get(child, "high"), "high") for _, _, child in children)
        low_price = min(_required_number(_get(child, "low"), "low") for _, _, child in children)
        totals = {
            field: math.fsum(
                _required_number(_get(child, field), field) for _, _, child in children
            )
            for field in _SUM_FIELDS
        }
        event = BarEvent(
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=totals["volume"],
            instrument_id=instrument,
            event_time_ns=bucket_ns,
        )
        bars.append(event)
        rows.append(
            {
                "instrument_id": instrument,
                "symbol": instrument,
                "ts_event": bucket_ns,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": totals["volume"],
                "quote_volume": totals["quote_volume"],
                "trade_count": int(round(totals["trade_count"])),
                "taker_buy_volume": totals["taker_buy_volume"],
                "taker_buy_quote_volume": totals["taker_buy_quote_volume"],
                "trading_date": trading_date or derive_trading_date(bucket_ns),
                "frequency": frequency,
                "volume_is_synthetic": False,
            }
        )

    return MinuteBarResult(
        bars=bars,
        rows=rows,
        frequency=frequency,
        volume_is_synthetic=False,
        issues=validate_bars(bars),
    )


__all__ = ["resample_standard_bars"]
