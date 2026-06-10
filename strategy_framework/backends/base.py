"""Execution-backend interface and a small, optional factory.

The backend boundary is deliberately tiny so strategies and the runner never
import an execution engine directly. A backend only needs to react to signals
and clean up at the end.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Names accepted in an ``execution.backend`` config value.
KNOWN_BACKENDS = ("signal_recorder", "simple_backtest", "paper", "nautilus_backtest", "nautilus_live")


@runtime_checkable
class ExecutionBackend(Protocol):
    """Minimal contract every execution backend satisfies."""

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        """Handle one ``(event, snapshot, signal)`` produced by the strategy."""
        ...

    def close(self) -> None:
        """Flush/finalize at the end of a run (print a summary, close files…)."""
        ...


def build_backend(execution_config: dict[str, Any] | None, spec_names: list[str]):
    """Return an :class:`ExecutionBackend` for an ``execution`` config, or ``None``.

    ``None`` (no ``execution`` block, or no ``backend`` key) preserves the legacy
    behaviour where ``run_strategy.py`` only prints/records via the output module.
    Imports are lazy so selecting one backend never loads the others (notably the
    Nautilus placeholders).
    """
    if not execution_config:
        return None
    name = execution_config.get("backend")
    if not name:
        return None

    if name in ("signal_recorder", "simple_backtest"):
        from strategy_framework.backends.simple_backtest import SimpleBacktestBackend

        return SimpleBacktestBackend(spec_names)
    if name == "paper":
        from strategy_framework.backends.paper import PaperBackend

        return PaperBackend(spec_names, execution_config)
    if name == "nautilus_backtest":
        from strategy_framework.backends.nautilus_backtest import NautilusBacktestBackend

        return NautilusBacktestBackend(spec_names, execution_config)
    if name == "nautilus_live":
        from strategy_framework.backends.nautilus_live import NautilusLiveBackend

        return NautilusLiveBackend(spec_names)

    raise ValueError(f"unknown execution backend {name!r}. Known: {KNOWN_BACKENDS}")
