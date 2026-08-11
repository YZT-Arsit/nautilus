"""Execution-intent layer.

Maps strategy signals (``BUY``/``SELL``/``HOLD``) to dependency-free *intents*
(:class:`OrderIntent` / :class:`PositionIntent`) via :class:`SignalToOrderPolicy`,
and models execution *results* (:class:`FillRecord`, :class:`PositionRecord`,
:class:`ExecutionReport`). This is the seam between signal generation and any
execution backend: strategies never create orders directly, and this layer never
imports Nautilus Trader.

Architecture::

    data_engine -> feature_engine -> strategy_framework -> strategies
                                  -> strategy_framework.execution (signal -> intent -> report)
                                  -> strategy_framework.backends   (intent -> backend)
"""
from strategy_framework.execution.intents import OrderIntent, PositionIntent
from strategy_framework.execution.duration_lag import (
    DurationExecutionAttempt,
    DurationLagTargetAdapter,
    PendingTarget,
)
from strategy_framework.execution.legacy_adapter import LegacyExecutionState
from strategy_framework.execution.reports import ExecutionReport, FillRecord, PositionRecord
from strategy_framework.execution.signal_policy import SignalToOrderPolicy

__all__ = [
    "OrderIntent",
    "PositionIntent",
    "SignalToOrderPolicy",
    "FillRecord",
    "PositionRecord",
    "ExecutionReport",
    "LegacyExecutionState",
    "PendingTarget",
    "DurationExecutionAttempt",
    "DurationLagTargetAdapter",
]
