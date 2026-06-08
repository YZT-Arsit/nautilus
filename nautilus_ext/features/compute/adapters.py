"""
Lightweight adapters converting existing event classes to feature-engine-compatible
duck-typed objects, plus a minimal HistoricalEventProvider interface for warmup.

The SpecFeatureEngine uses duck typing: it reads event attributes by name
(event_type, instrument_id, event_time_ns, receive_time_ns, source, open,
close, bid_price, …). Any object with the right attributes works.

Existing event classes (BarEvent, QuoteTickEvent) from nautilus_ext.data.events
use a datetime for ts_event rather than integer nanoseconds, and carry no
event_type attribute. The adapter functions below convert them without touching
the original classes.

Quick start
-----------
    from nautilus_ext.features.compute.adapters import (
        adapt_bar_event, adapt_quote_tick_event,
        InMemoryEventProvider,
    )

    # One-shot: adapt a single event and pass it to the engine
    adapted = adapt_bar_event(my_bar_event)
    engine.on_event(adapted)

    # Batch warmup via provider
    provider = InMemoryEventProvider([adapt_bar_event(b) for b in historical_bars])
    engine.warmup(provider.iter_events(instrument_id="BTC/USDT", input_type="bar"))

If your event class already has event_type, event_time_ns, and receive_time_ns
with the correct semantics, pass it directly to SpecFeatureEngine — no adapter
needed.

Recommended production flow
---------------------------
    DataFeed → adapt_*() / custom MarketEvent → SpecFeatureEngine.on_event()
                                                    ↓
                                               FeatureSnapshot
                                                    ↓
                                              Strategy.on_snapshot()
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Adapted event dataclasses — duck-typed for SpecFeatureEngine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BarMarketEvent:
    """Bar event adapted for SpecFeatureEngine.

    All timestamp fields are nanoseconds POSIX. ``event_type`` is always
    ``"bar"`` — the canonical value expected by SpecFeatureEngine routing.
    """

    instrument_id: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    event_type: str
    event_time_ns: int
    receive_time_ns: int | None = None
    source: str | None = None
    volume_type: str = "unknown"


@dataclass(frozen=True)
class QuoteMarketEvent:
    """Quote-tick event adapted for SpecFeatureEngine.

    All timestamp fields are nanoseconds POSIX. ``event_type`` is always
    ``"quote"`` — the canonical value expected by SpecFeatureEngine routing.
    """

    instrument_id: str
    bid_price: float
    ask_price: float
    bid_size: float | None
    ask_size: float | None
    event_type: str
    event_time_ns: int
    receive_time_ns: int | None = None
    source: str | None = None


@dataclass(frozen=True)
class TradeMarketEvent:
    """Trade-tick event for SpecFeatureEngine.

    Use when your data source provides individual trade ticks (price + size).
    ``event_type`` is always ``"trade"`` — the canonical value.
    """

    instrument_id: str
    price: float
    size: float
    event_type: str
    event_time_ns: int
    receive_time_ns: int | None = None
    source: str | None = None
    side: str | None = None


# ---------------------------------------------------------------------------
# Timestamp conversion helpers
# ---------------------------------------------------------------------------

def _datetime_to_ns(dt: Any) -> int:
    """Convert a datetime, int, or float to nanoseconds POSIX.

    Handles:
    - ``datetime`` objects (with or without timezone) via ``.timestamp()``.
    - Integer / float already in nanoseconds — passed through as ``int``.
    - ``None`` or unsupported type → 0.
    """
    if dt is None:
        return 0
    if hasattr(dt, "timestamp"):
        # datetime object — .timestamp() returns float POSIX seconds
        return int(dt.timestamp() * 1_000_000_000)
    if isinstance(dt, (int, float)):
        return int(dt)
    return 0


# ---------------------------------------------------------------------------
# Adapter functions
# ---------------------------------------------------------------------------

def adapt_bar_event(bar: Any) -> BarMarketEvent:
    """Convert a BarEvent (or any bar-like object) to a BarMarketEvent.

    Timestamp resolution order for ``event_time_ns``:
    1. ``bar.event_time_ns`` — present and non-None (nanoseconds, used as-is).
    2. ``bar.ts_event`` — converted via ``_datetime_to_ns()``; works for both
       ``datetime`` objects and integer millisecond timestamps.

    Timestamp resolution order for ``receive_time_ns``:
    1. ``bar.receive_time_ns`` — present and non-None.
    2. ``bar.ts_init`` — converted via ``_datetime_to_ns()``.
    3. Fallback: ``event_time_ns`` (assumes no network latency gap).

    Parameters
    ----------
    bar : Any
        Any object with bar fields (open, high, low, close, volume,
        instrument_id, ts_event or event_time_ns, …).

    Returns
    -------
    BarMarketEvent
        Fully populated, frozen dataclass safe to pass to SpecFeatureEngine.
    """
    event_time_ns = getattr(bar, "event_time_ns", None)
    if event_time_ns is None:
        event_time_ns = _datetime_to_ns(getattr(bar, "ts_event", None))
    event_time_ns = int(event_time_ns)

    receive_time_ns = getattr(bar, "receive_time_ns", None)
    if receive_time_ns is None:
        ts_init = getattr(bar, "ts_init", None)
        receive_time_ns = _datetime_to_ns(ts_init) if ts_init is not None else event_time_ns
    receive_time_ns = int(receive_time_ns)

    return BarMarketEvent(
        instrument_id=str(getattr(bar, "instrument_id", "unknown")),
        open=float(getattr(bar, "open", 0.0)),
        high=float(getattr(bar, "high", 0.0)),
        low=float(getattr(bar, "low", 0.0)),
        close=float(getattr(bar, "close", 0.0)),
        volume=float(getattr(bar, "volume", 0.0)),
        event_type="bar",
        event_time_ns=event_time_ns,
        receive_time_ns=receive_time_ns,
        source=getattr(bar, "source", None),
        volume_type=str(getattr(bar, "volume_type", "unknown")),
    )


def adapt_quote_tick_event(quote: Any) -> QuoteMarketEvent:
    """Convert a QuoteTickEvent (or any quote-like object) to a QuoteMarketEvent.

    Same timestamp resolution chain as ``adapt_bar_event``.
    ``event_type`` is always ``"quote"`` (canonical, expected by the engine).

    Parameters
    ----------
    quote : Any
        Any object with bid_price, ask_price, bid_size, ask_size,
        instrument_id, and ts_event or event_time_ns.

    Returns
    -------
    QuoteMarketEvent
        Fully populated, frozen dataclass safe to pass to SpecFeatureEngine.
    """
    event_time_ns = getattr(quote, "event_time_ns", None)
    if event_time_ns is None:
        event_time_ns = _datetime_to_ns(getattr(quote, "ts_event", None))
    event_time_ns = int(event_time_ns)

    receive_time_ns = getattr(quote, "receive_time_ns", None)
    if receive_time_ns is None:
        ts_init = getattr(quote, "ts_init", None)
        receive_time_ns = _datetime_to_ns(ts_init) if ts_init is not None else event_time_ns
    receive_time_ns = int(receive_time_ns)

    bid_size_raw = getattr(quote, "bid_size", None)
    ask_size_raw = getattr(quote, "ask_size", None)

    return QuoteMarketEvent(
        instrument_id=str(getattr(quote, "instrument_id", "unknown")),
        bid_price=float(getattr(quote, "bid_price", 0.0)),
        ask_price=float(getattr(quote, "ask_price", 0.0)),
        bid_size=float(bid_size_raw) if bid_size_raw is not None else None,
        ask_size=float(ask_size_raw) if ask_size_raw is not None else None,
        event_type="quote",
        event_time_ns=event_time_ns,
        receive_time_ns=receive_time_ns,
        source=getattr(quote, "source", None),
    )


# ---------------------------------------------------------------------------
# Historical event provider
# ---------------------------------------------------------------------------

@runtime_checkable
class HistoricalEventProvider(Protocol):
    """Minimal protocol for feeding historical events to SpecFeatureEngine.warmup().

    Any class with an ``iter_events()`` method satisfies this protocol without
    explicit inheritance (structural duck-typing via ``typing.Protocol``).

    Implementations
    ---------------
    - ``InMemoryEventProvider`` — list-backed, for tests and prototyping.
    - Production: parquet catalog, Redis stream, SQL query, etc.

    Usage
    -----
    Pass the result of ``iter_events()`` directly to ``engine.warmup()``:

        provider = MyProvider(...)
        engine.warmup(provider.iter_events(
            instrument_id="BTC/USDT",
            input_type="bar",
            start_ns=t0_ns,
            end_ns=t1_ns,
        ))

    Ordering contract
    -----------------
    Events MUST be yielded in ascending ``event_time_ns`` order. The warmup
    path does NOT sort — it calls ``feature.update()`` in iteration order and
    advances watermarks accordingly. Out-of-order events during warmup will
    produce incorrect watermarks for subsequent live events.
    """

    def iter_events(
        self,
        instrument_id: str | None = None,
        input_type: str | None = None,
        start_ns: int = 0,
        end_ns: int | None = None,
    ) -> Iterable:
        """Yield events matching the given filters in ascending event_time_ns order.

        Parameters
        ----------
        instrument_id : str | None
            Filter by instrument identifier; ``None`` yields all instruments.
        input_type : str | None
            Filter by canonical input_type (``"bar"``, ``"quote"``, ``"trade"``,
            ``"book_delta"``); ``None`` yields all event types.
        start_ns : int
            Inclusive lower bound on ``event_time_ns`` (nanoseconds POSIX).
        end_ns : int | None
            Exclusive upper bound; ``None`` means no upper limit.
        """
        ...


class InMemoryEventProvider:
    """In-memory event provider for tests, prototyping, and small-scale warmup.

    Stores adapted events in a list and filters by instrument_id, input_type,
    and time range on iteration. Assumes events are already in ascending
    ``event_time_ns`` order (as produced by ``adapt_bar_event`` etc.).

    Parameters
    ----------
    events : Iterable
        Initial event sequence. Copied into an internal list at construction.

    Usage
    -----
        from nautilus_ext.features.compute.adapters import (
            InMemoryEventProvider, adapt_bar_event,
        )
        adapted = [adapt_bar_event(b) for b in raw_bars]
        provider = InMemoryEventProvider(adapted)
        engine.warmup(provider.iter_events(instrument_id="BTC/USDT"))
    """

    def __init__(self, events: Iterable = ()) -> None:
        self._events: list = list(events)

    def append(self, event: Any) -> None:
        """Append one event to the provider."""
        self._events.append(event)

    def __len__(self) -> int:
        return len(self._events)

    def iter_events(
        self,
        instrument_id: str | None = None,
        input_type: str | None = None,
        start_ns: int = 0,
        end_ns: int | None = None,
    ) -> Iterable:
        """Yield matching events in insertion order (ascending event_time_ns assumed).

        Filtering is O(n) over the internal list. For large providers use a
        production implementation backed by an indexed store.
        """
        for event in self._events:
            if instrument_id is not None:
                ev_iid = getattr(event, "instrument_id", None)
                if ev_iid != instrument_id:
                    continue
            if input_type is not None:
                # Normalise via the engine's canonical resolver so vendor aliases
                # ("quote_tick", "orderbook") match correctly.
                from nautilus_ext.features.compute.engine import input_type_for_event
                canonical = input_type_for_event(event)
                if canonical != input_type:
                    continue
            ev_time_ns: int = int(getattr(event, "event_time_ns", 0) or 0)
            if ev_time_ns < start_ns:
                continue
            if end_ns is not None and ev_time_ns >= end_ns:
                continue
            yield event
