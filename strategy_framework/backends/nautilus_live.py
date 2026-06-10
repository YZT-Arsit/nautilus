"""Placeholder adapter: custom framework -> Nautilus Trader live execution.

This is a SKELETON. It does not import Nautilus Trader, opens no connections, and
requires no exchange. It marks where the optional Nautilus *live* backend will
plug in later.

Future integration (TODO) will translate our outputs into live order/execution
calls:

    custom signal (BUY/SELL/HOLD) -> target position / order intent
    target position               -> nautilus_trader live execution & order APIs
                                     (submit_order / modify / cancel via a
                                      TradingNode + ExecutionClient)

Implementation notes for later:
* This adapter owns all Nautilus coupling; strategies and the feature engine
  stay Nautilus-agnostic.
* Import Nautilus and open connectors lazily inside methods — never at module
  import time — so importing this module is safe in any environment.
* Real live trading also needs risk limits and credentials; those belong here /
  in config, not in the strategy.
"""
from __future__ import annotations

from typing import Any

_NOT_IMPLEMENTED = (
    "NautilusLiveBackend is a placeholder. Nautilus Trader live execution "
    "integration is not implemented yet; real live trading is out of scope."
)


class NautilusLiveBackend:
    """Skeleton backend. Constructs cheaply; raises when actually driven."""

    def __init__(self, spec_names: list[str] | None = None) -> None:
        self._spec_names = list(spec_names or [])
        # TODO: configure a nautilus_trader TradingNode / ExecutionClient (lazily).

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        # TODO: map signal -> target position -> live order intent.
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def close(self) -> None:
        # TODO: flatten positions / disconnect cleanly when integration lands.
        raise NotImplementedError(_NOT_IMPLEMENTED)
