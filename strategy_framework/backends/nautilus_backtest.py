"""Placeholder adapter: custom framework -> Nautilus Trader backtest engine.

This is a SKELETON. It does not import Nautilus Trader and performs no execution.
It marks where the optional Nautilus backtest backend will plug in later, so the
ordinary user-facing path (run_strategy.py -> strategies -> backends) never
depends on Nautilus core today.

Future integration (TODO) will translate our lightweight objects into Nautilus
backtest inputs:

    custom BarEvent        -> nautilus_trader Bar / data objects
    custom FeatureSnapshot -> strategy state fed to a Nautilus Strategy
    custom signal (BUY/SELL/HOLD) -> Nautilus order intents / submit_order(...)

and run them through ``nautilus_trader.backtest`` (BacktestEngine /
BacktestNode) to produce fills, positions, and PnL.

Implementation notes for later:
* Keep the translation here (an adapter), never inside nautilus_ext/features
  compute or the strategies — those must stay Nautilus-agnostic.
* Import Nautilus lazily inside methods so importing this module stays cheap and
  does not require Nautilus to be compiled/installed.
"""
from __future__ import annotations

from typing import Any

_NOT_IMPLEMENTED = (
    "NautilusBacktestBackend is a placeholder. Nautilus Trader backtest "
    "integration is not implemented yet; use 'signal_recorder' or 'paper'."
)


class NautilusBacktestBackend:
    """Skeleton backend. Constructs cheaply; raises when actually driven."""

    def __init__(self, spec_names: list[str] | None = None) -> None:
        self._spec_names = list(spec_names or [])
        # TODO: build/configure a nautilus_trader BacktestEngine here (lazily).

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        # TODO: translate (event, snapshot, signal) into Nautilus order intents.
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def close(self) -> None:
        # TODO: run the engine / collect results when integration lands.
        raise NotImplementedError(_NOT_IMPLEMENTED)
