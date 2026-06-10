"""Adapter: custom framework -> Nautilus Trader backtest engine.

``NautilusBacktestBackend`` currently provides:

* a Nautilus-backend **boundary** (intent -> execution, isolated here);
* **intent collection** from strategy signals (via :class:`SignalToOrderPolicy`);
* a dependency-free **simulated fill/PnL reference path** (``mode="simulated"``,
  the default) backed by :class:`IntentFillSimulator`;
* a **lazy placeholder** for future native Nautilus ``BacktestEngine`` integration
  (``mode="nautilus_native"``).

It does **not** yet claim full Nautilus-native execution: ``mode="nautilus_native"``
raises a clear ``NotImplementedError`` when driven, until the native path is
implemented. No real orders are sent; no exchange/live trading. All Nautilus
imports are **lazy** (inside the optional translation helpers only) so importing
this module never requires Nautilus to be installed/compiled.

Config (``execution:`` block)::

    backend: nautilus_backtest
    quantity: 1.0
    sell_means: flat        # or "short"
    mode: simulated         # or "nautilus_native"
    allow_short: false
    price_field: close
"""
from __future__ import annotations

from typing import Any

from strategy_framework.execution.intents import OrderIntent, PositionIntent
from strategy_framework.execution.reports import ExecutionReport
from strategy_framework.execution.signal_policy import SignalToOrderPolicy

_SUPPORTED_MODES = ("simulated", "nautilus_native")

_NATIVE_NOT_IMPLEMENTED = (
    "NautilusBacktestBackend(mode='nautilus_native') is not implemented yet: a "
    "native Nautilus BacktestEngine integration is the next stage. Use "
    "mode='simulated' (the default) for a dependency-free fill/PnL report."
)


def try_translate_to_nautilus_order(intent: OrderIntent):
    """Optionally translate an intent into a Nautilus order object.

    Returns ``None`` when Nautilus Trader is unavailable (the normal case here) or
    until translation is implemented. Nautilus is imported **lazily** so the module
    import graph never depends on it.
    """
    try:
        import nautilus_trader  # noqa: F401
    except ImportError:
        return None
    # TODO: build a nautilus_trader order from `intent` (instrument, side, qty).
    return None


def try_build_nautilus_backtest_engine(config: dict[str, Any] | None):
    """Optionally build a native Nautilus ``BacktestEngine``.

    Returns ``None`` when Nautilus Trader is unavailable or until native
    integration is implemented. Nautilus is imported **lazily** here; this is never
    run by default (only ``mode="nautilus_native"`` reaches it).
    """
    try:
        import nautilus_trader  # noqa: F401
    except ImportError:
        return None
    # TODO: construct & configure a nautilus_trader.backtest BacktestEngine.
    return None


class NautilusBacktestBackend:
    """Map signals -> intents and (in simulated mode) -> fills / positions / PnL."""

    def __init__(self, spec_names: list[str] | None = None, execution_config: dict[str, Any] | None = None) -> None:
        self._spec_names = list(spec_names or [])
        cfg = execution_config or {}
        self._mode = cfg.get("mode", "simulated")
        if self._mode not in _SUPPORTED_MODES:
            raise ValueError(
                f"unknown nautilus_backtest mode {self._mode!r}. Supported: {_SUPPORTED_MODES}"
            )
        self._policy = SignalToOrderPolicy(
            quantity=float(cfg.get("quantity", 1.0)),
            sell_means=cfg.get("sell_means", "flat"),
            spec_names=self._spec_names,
        )
        self._intents: list[OrderIntent | PositionIntent] = []
        self._native_started = False  # lazy native build guard

        self._simulator = None
        if self._mode == "simulated":
            # Imported here (not at module top) to keep the boundary clean; it is
            # a pure-Python sibling, no Nautilus dependency.
            from strategy_framework.backends.nautilus_simulation import IntentFillSimulator

            self._simulator = IntentFillSimulator(
                default_price_field=cfg.get("price_field", "close"),
                allow_short=bool(cfg.get("allow_short", False)),
                backend="nautilus_backtest",
            )

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        intent = self._policy.on_signal(event, snapshot, signal)
        if intent is None:
            return
        self._intents.append(intent)

        if self._mode == "simulated":
            self._simulator.on_intent(intent, event)
        elif self._mode == "nautilus_native":
            # Lazy native attempt; clearly unsupported until implemented.
            if not self._native_started:
                self._native_started = True
                try_build_nautilus_backtest_engine(None)
            raise NotImplementedError(_NATIVE_NOT_IMPLEMENTED)

    def intents(self) -> list[OrderIntent | PositionIntent]:
        return list(self._intents)

    def summary(self) -> dict[str, Any]:
        """Intent-level counts/instruments (unchanged shape; see report() for PnL)."""
        buys = sum(1 for i in self._intents if getattr(i, "side", None) == "BUY")
        sells = sum(
            1 for i in self._intents
            if getattr(i, "side", None) == "SELL" or getattr(i, "target", None) == "FLAT"
        )
        instruments = sorted({i.instrument_id for i in self._intents if i.instrument_id})
        return {
            "total": len(self._intents),
            "buy": buys,
            "sell": sells,
            "instruments": instruments,
        }

    def report(self) -> ExecutionReport:
        """Full fills/positions/PnL report (simulated mode)."""
        if self._mode != "simulated":
            raise NotImplementedError(_NATIVE_NOT_IMPLEMENTED)
        return self._simulator.report()

    def close(self) -> None:
        if self._mode != "simulated":
            raise NotImplementedError(_NATIVE_NOT_IMPLEMENTED)
        rep = self._simulator.report()
        instruments = sorted({i.instrument_id for i in self._intents if i.instrument_id})
        print(f"[nautilus_backtest] mode=simulated (no real orders, reference fill model)")
        print(f"  intents: total={rep.total_intents} instruments={instruments}")
        print(f"  fills:   total={rep.total_fills}")
        print(f"  pnl:     realized={rep.realized_pnl:.4f} unrealized={rep.unrealized_pnl:.4f}")
        for p in rep.positions:
            print(
                f"  position: {p.instrument_id} qty={p.quantity} avg={p.avg_price:.4f} "
                f"mkt={p.market_price:.4f} uPnL={p.unrealized_pnl:.4f}"
            )
