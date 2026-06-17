"""Replay-parity helpers: historical TradeEvent vs live-normalized TradeEvent.

Validates that a Binance aggTrade flowing through the **live** normalizer yields
the same :class:`~data_engine.events.TradeEvent` semantics as the **historical**
loader produces for the same trade.  Pure stdlib -- no network, no pyarrow, no
``nautilus_trader``.

Two principled, expected differences are handled explicitly:

* ``source`` -- historical loader emits ``"parquet_trades"``; the live normalizer
  emits ``"binance_ws_aggTrade"``.  Excluded from the parity comparison.
* ``event_time_ns`` resolution -- the Binance Vision archive carries microsecond
  timestamps, but a live WS ``aggTrade`` ``T`` is **milliseconds**.  Parity is
  therefore checked at millisecond resolution; sub-millisecond archive precision
  is an expected, documented divergence (a real live feed cannot reproduce it).
"""
from __future__ import annotations

from typing import Any

_MS_NS = 1_000_000

# Fields compared exactly (source handled separately; event_time at resolution).
PARITY_FIELDS = [
    "event_type", "instrument_id", "price", "quantity",
    "quote_quantity", "side", "is_buyer_maker", "trade_id",
]


def standard_trade_to_agg_message(
    *,
    symbol: str,
    event_time_ns: int,
    price: float,
    quantity: float,
    agg_trade_id: Any,
    is_buyer_maker: Any,
    first_trade_id: Any = None,
    last_trade_id: Any = None,
    wrap: bool = False,
) -> dict:
    """Build a faithful Binance ``aggTrade`` WS message from a StandardTrade row.

    ``T``/``E`` are milliseconds (as a real WS feed emits), derived by truncating
    ``event_time_ns``.  ``wrap=True`` returns a combined-stream envelope.
    """
    t_ms = int(event_time_ns) // _MS_NS
    msg = {
        "e": "aggTrade",
        "E": t_ms,
        "s": symbol,
        "a": agg_trade_id,
        "p": repr(float(price)),
        "q": repr(float(quantity)),
        "f": first_trade_id,
        "l": last_trade_id,
        "T": t_ms,
        "m": bool(is_buyer_maker) if is_buyer_maker is not None else False,
        "M": True,
    }
    if wrap:
        return {"stream": f"{symbol.lower()}@aggTrade", "data": msg}
    return msg


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


def compare_trade_events(
    a,
    b,
    *,
    ignore_source: bool = True,
    time_resolution_ns: int = _MS_NS,
) -> tuple[bool, list[tuple[str, Any, Any]]]:
    """Compare two TradeEvents; return ``(is_match, diffs)``.

    ``a`` is the historical (ground-truth) event, ``b`` the live-normalized one.
    ``event_time_ns`` is compared floored to ``time_resolution_ns`` (default 1 ms).
    ``source`` is compared only when ``ignore_source=False``.
    """
    diffs: list[tuple[str, Any, Any]] = []
    for f in PARITY_FIELDS:
        va, vb = getattr(a, f), getattr(b, f)
        if not _values_equal(va, vb):
            diffs.append((f, va, vb))
    ta = a.event_time_ns // time_resolution_ns
    tb = b.event_time_ns // time_resolution_ns
    if ta != tb:
        diffs.append(("event_time_ns@resolution", a.event_time_ns, b.event_time_ns))
    if not ignore_source and getattr(a, "source", None) != getattr(b, "source", None):
        diffs.append(("source", getattr(a, "source", None), getattr(b, "source", None)))
    return (not diffs, diffs)
