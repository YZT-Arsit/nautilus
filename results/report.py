"""Results reporting façade.

The dependency-free report writer/accountant lives in
``strategy_framework.execution.backtest_report`` (it is consumed directly by the
Nautilus backtest backend at ``close()``). ``results`` is the public results
layer, so re-export it here — new results tooling imports from ``results``.

Artifacts written per run (``outputs/backtests/<run>/``):
``signals/intents/fills/trades/positions/equity_curve.csv`` + ``metrics.json`` +
``report.md`` + ``report.json``.
"""
from __future__ import annotations

from strategy_framework.execution.backtest_report import write_backtest_report

__all__ = ["write_backtest_report"]
