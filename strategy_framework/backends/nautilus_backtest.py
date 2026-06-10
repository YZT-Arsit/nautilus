"""Adapter: custom framework -> Nautilus Trader backtest engine (MVP).

This is the **first executable step**. The backend collects order/position
*intents* derived from strategy signals (via the shared
:class:`SignalToOrderPolicy`) and prints a summary. It does **not** yet run a
Nautilus ``BacktestEngine``, simulate fills, or compute PnL — that is the next
stage.

A clearly separated, optional translation hook (:func:`try_translate_to_nautilus_order`)
marks where intents will become Nautilus order objects. Nautilus Trader is
imported **lazily inside that function only** — importing this module stays cheap
and never requires Nautilus to be installed/compiled.

Future integration (TODO) will translate:

    custom OrderIntent / PositionIntent -> nautilus_trader order objects
    + a BacktestEngine run -> fills, positions, PnL.
"""
from __future__ import annotations

from typing import Any

from strategy_framework.execution.intents import OrderIntent, PositionIntent
from strategy_framework.execution.signal_policy import SignalToOrderPolicy


def try_translate_to_nautilus_order(intent: OrderIntent):
    """Optionally translate an intent into a Nautilus order object.

    Returns ``None`` when Nautilus Trader is unavailable (the normal case in this
    repo/tests) or until translation is implemented. Nautilus is imported lazily
    here so the module import graph never depends on it.
    """
    try:
        import nautilus_trader  # noqa: F401
    except ImportError:
        return None
    # TODO: build a nautilus_trader order from `intent` (instrument, side, qty).
    return None


class NautilusBacktestBackend:
    """Collects intents from signals and summarizes them (MVP, no fills/PnL)."""

    def __init__(self, spec_names: list[str] | None = None, execution_config: dict[str, Any] | None = None) -> None:
        self._spec_names = list(spec_names or [])
        cfg = execution_config or {}
        self._policy = SignalToOrderPolicy(
            quantity=float(cfg.get("quantity", 1.0)),
            sell_means=cfg.get("sell_means", "flat"),
            spec_names=self._spec_names,
        )
        self._intents: list[OrderIntent | PositionIntent] = []

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        intent = self._policy.on_signal(event, snapshot, signal)
        if intent is None:
            return
        self._intents.append(intent)

    def intents(self) -> list[OrderIntent | PositionIntent]:
        return list(self._intents)

    def summary(self) -> dict[str, Any]:
        """Return counts/instruments for the collected intents."""
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

    def close(self) -> None:
        s = self.summary()
        print(
            f"[nautilus_backtest] intents: total={s['total']} "
            f"BUY={s['buy']} SELL={s['sell']} instruments={s['instruments']} "
            f"(MVP: intent collection only, no fills/PnL yet)"
        )
