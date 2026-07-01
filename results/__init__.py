"""results — the self-built results layer: report / run_uid / charts / viewer.

* :mod:`results.run_uid` — deterministic ``run_uid`` (reuse anchor).
* :mod:`results.report` — ``write_backtest_report`` (report/metrics artifacts).
* :mod:`results.charts` — render equity/drawdown/pnl/position PNGs (matplotlib-guarded).
* :mod:`results.viewer` — build a local, read-only static HTML view of a run/batch.

See ``docs/PLATFORM_ARCHITECTURE.md`` §6.
"""
from __future__ import annotations

from results.run_uid import build_run_uid, params_hash, window_label

__all__ = ["build_run_uid", "params_hash", "window_label"]
