"""Live event source boundary.

Defines the interface a *real* live feed should implement later, plus a
synthetic implementation used by the ``live_synthetic`` data mode. This file
deliberately has **no** network or exchange dependencies — it only marks where
real connectors plug in.

A real source (e.g. an exchange websocket) should implement
:class:`LiveEventSource`:

* ``warmup()`` — return a finite iterable of historical events to pre-heat the
  feature engine before live trading begins.
* ``stream()`` — return a (possibly unbounded) iterable of live events.

Such a source can then be wired into ``strategy_framework/data_loaders.py`` as a
new ``mode`` without changing ``run_strategy.py``.
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from strategy_framework.data_loaders import load_live_synthetic


@runtime_checkable
class LiveEventSource(Protocol):
    """Protocol every live feed must satisfy."""

    def warmup(self) -> Iterable[Any]: ...

    def stream(self) -> Iterable[Any]: ...


class SyntheticLiveEventSource:
    """A dependency-free :class:`LiveEventSource` over the live_synthetic path.

    Wraps :func:`strategy_framework.data_loaders.load_live_synthetic` so the same
    skeleton behaviour is also available behind the source interface that real
    connectors will implement.
    """

    def __init__(self, data_config: dict[str, Any] | None = None) -> None:
        self._config = dict(data_config or {})

    def warmup(self) -> list[Any]:
        warmup_events, _ = load_live_synthetic(self._config)
        return warmup_events

    def stream(self) -> Iterator[Any]:
        _, live_events = load_live_synthetic(self._config)
        return iter(live_events)
