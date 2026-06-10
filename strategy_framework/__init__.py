"""Reusable orchestration glue shared by every strategy.

This package is the *internal* user-facing framework: the registry, data
loaders, output formatting, the backtest signal recorder, and the live-source
boundary. Strategy definitions live in the top-level ``strategies/`` package;
the shared entry point is the top-level ``run_strategy.py``.

Import submodules directly (e.g. ``from strategy_framework.registry import
get_entry``) — this package keeps ``__init__`` free of imports to avoid cycles
with the strategy packages it loads.
"""
