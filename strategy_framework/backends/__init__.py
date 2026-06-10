"""Execution backend skeleton.

A *backend* receives the strategy's ``(event, snapshot, signal)`` stream and
decides what to do with it: record signals, log paper orders, or (in the future)
drive a real backtest/live engine. This keeps ``run_strategy.py`` and the
strategy logic decoupled from execution.

Today only the dependency-free backends are functional:

* ``signal_recorder`` / ``simple_backtest`` — :class:`SimpleBacktestBackend`
* ``paper`` — :class:`PaperBackend` (logs intended orders, no fills, no PnL)

The Nautilus backends are intentional placeholders — see
``nautilus_backtest.py`` and ``nautilus_live.py``. They mark where the optional
Nautilus Trader execution/backtest engine plugs in later; importing them is
cheap and does not pull in heavy Nautilus modules or require an exchange.

Use :func:`build_backend` to select one from an ``execution:`` config block.
"""
from strategy_framework.backends.base import ExecutionBackend, build_backend

__all__ = ["ExecutionBackend", "build_backend"]
