"""Adapter: custom framework -> Nautilus Trader backtest path.

``NautilusBacktestBackend`` is the boundary where strategy *signals* become
*intents* (via :class:`SignalToOrderPolicy`) and then *fills* - from one of two
fill sources, selected by ``execution.mode``:

* ``mode="simulated"`` (default): a dependency-free reference fill model
  (:class:`IntentFillSimulator`). No Nautilus required.
* ``mode="nautilus_native"``: a **real** Nautilus ``BacktestEngine`` run, via the
  lazy :mod:`strategy_framework.backends.nautilus_native` adapter. Requires the
  ``nautilus_trader`` package (present on the backtest server); when it is absent
  the backend raises a clear :class:`NautilusUnavailableError` - **not** a
  placeholder ``NotImplementedError``.

Both modes feed the *same* dependency-free analytics/report writer
(:mod:`strategy_framework.execution.backtest_report`), so the artifact set
(``signals/intents/trades/positions/equity_curve/metrics.json/report.md``) is
identical in shape regardless of which engine produced the fills.

All Nautilus imports stay inside the native adapter; this module never imports
``nautilus_trader`` at top level.

Config (``execution:`` block)::

    backend: nautilus_backtest
    mode: nautilus_native     # or "simulated"
    initial_cash: 100000
    quantity: 1.0
    sell_means: flat          # or "short"
    allow_short: false
    price_field: close
    fee_rate: 0.0005
    slippage_bps: 1.0
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from strategy_framework.execution.intents import OrderIntent, PositionIntent
from strategy_framework.execution.reports import ExecutionReport
from strategy_framework.execution.signal_policy import SignalToOrderPolicy

_SUPPORTED_MODES = ("simulated", "nautilus_native")


def try_translate_to_nautilus_order(intent: OrderIntent):
    """Best-effort translation of one intent to a Nautilus order (or ``None``).

    Retained for backward compatibility. The production native path lives in
    :func:`strategy_framework.backends.nautilus_native.run_native_backtest`, which
    submits orders inside a replay strategy. Nautilus is imported lazily here.
    """
    try:
        import nautilus_trader  # noqa: F401
    except ImportError:
        return None
    return None


def try_build_nautilus_backtest_engine(config: dict[str, Any] | None):
    """Deprecated shim retained for backward compatibility.

    The production native engine is built inside
    :func:`strategy_framework.backends.nautilus_native.run_native_backtest`. This
    helper always returns ``None``; Nautilus is imported lazily.
    """
    try:
        import nautilus_trader  # noqa: F401
    except ImportError:
        return None
    return None


def _intent_action(intent: Any) -> tuple[str, float] | None:
    """Map an intent to a ``(action, quantity)`` pair for the native replay."""
    if isinstance(intent, OrderIntent):
        return (intent.side, float(intent.quantity))
    if isinstance(intent, PositionIntent):
        if intent.target == "FLAT":
            return ("FLAT", 0.0)
    return None


class NautilusBacktestBackend:
    """Signals -> intents -> fills (simulated or native) -> report artifacts."""

    def __init__(
        self,
        spec_names: list[str] | None = None,
        execution_config: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._spec_names = list(spec_names or [])
        cfg = execution_config or {}
        ctx = context or {}

        self._mode = cfg.get("mode", "simulated")
        if self._mode not in _SUPPORTED_MODES:
            raise ValueError(
                f"unknown nautilus_backtest mode {self._mode!r}. Supported: {_SUPPORTED_MODES}"
            )

        self._quantity = float(cfg.get("quantity", 1.0))
        self._initial_cash = float(cfg.get("initial_cash", 100_000.0))
        self._allow_short = bool(cfg.get("allow_short", False))
        self._price_field = cfg.get("price_field", "close")
        self._fee_rate = float(cfg.get("fee_rate", 0.0))
        self._slippage_bps = float(cfg.get("slippage_bps", 0.0))
        self._policy = SignalToOrderPolicy(
            quantity=self._quantity,
            sell_means=cfg.get("sell_means", "flat"),
            spec_names=self._spec_names,
        )

        # Streams collected during the run (fed to the report writer at close()).
        self._intents: list[OrderIntent | PositionIntent] = []
        self._bar_rows: list[dict[str, Any]] = []
        self._signal_rows: list[dict[str, Any]] = []
        self._intent_rows: list[dict[str, Any]] = []
        self._intents_by_ts: dict[int, tuple[str, float]] = {}

        # Run/output context (provided by run_strategy; may be empty for unit use).
        data_cfg = ctx.get("data", {}) or {}
        self._instrument_id = data_cfg.get("instrument_id", "BTCUSDT.BINANCE")
        self._run_name = ctx.get("run_name") or cfg.get("run_name") or "backtest"
        self._config = ctx.get("config")
        self._output_dir = self._resolve_output_dir(ctx)
        self.last_result = None  # populated by close() when a report is written

        # The simulated reference fill model (only built when needed).
        self._simulator = None
        if self._mode == "simulated":
            from strategy_framework.backends.nautilus_simulation import IntentFillSimulator

            self._simulator = IntentFillSimulator(
                default_price_field=self._price_field,
                allow_short=self._allow_short,
                backend="nautilus_backtest",
            )

    # -- setup helpers -------------------------------------------------------

    def _resolve_output_dir(self, ctx: dict[str, Any]) -> Path | None:
        output_cfg = ctx.get("output") or {}
        root = output_cfg.get("root")
        if root is None:
            return None
        base = Path(root)
        if not base.is_absolute():
            repo_root = ctx.get("repo_root")
            base = (Path(repo_root) / base) if repo_root else base
        return base / self._run_name

    # -- streaming -----------------------------------------------------------

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        ts = getattr(event, "event_time_ns", None)
        instrument_id = getattr(event, "instrument_id", None)
        close = getattr(event, "close", None)

        self._bar_rows.append(
            {
                "event_time_ns": ts,
                "instrument_id": instrument_id,
                "open": getattr(event, "open", close),
                "high": getattr(event, "high", close),
                "low": getattr(event, "low", close),
                "close": close,
                "volume": getattr(event, "volume", 0.0),
            }
        )
        value = getattr(snapshot, "value", lambda *_: None)
        self._signal_rows.append(
            {
                "event_time_ns": ts,
                "instrument_id": instrument_id,
                "signal": signal,
                "close": close,
                **{name: value(name) for name in self._spec_names},
            }
        )

        intent = self._policy.on_signal(event, snapshot, signal)
        if intent is None:
            return
        self._intents.append(intent)
        action = _intent_action(intent)
        self._intent_rows.append(
            {
                "event_time_ns": ts,
                "instrument_id": intent.instrument_id,
                "action": action[0] if action else None,
                "quantity": getattr(intent, "quantity", 0.0),
                "reason": getattr(intent, "reason", ""),
            }
        )
        if self._mode == "simulated":
            self._simulator.on_intent(intent, event)
        elif self._mode == "nautilus_native" and action is not None and ts is not None:
            self._intents_by_ts[int(ts)] = action

    # -- introspection (unchanged shape) -------------------------------------

    def intents(self) -> list[OrderIntent | PositionIntent]:
        return list(self._intents)

    def summary(self) -> dict[str, Any]:
        buys = sum(1 for i in self._intents if getattr(i, "side", None) == "BUY")
        sells = sum(
            1 for i in self._intents
            if getattr(i, "side", None) == "SELL" or getattr(i, "target", None) == "FLAT"
        )
        instruments = sorted({i.instrument_id for i in self._intents if i.instrument_id})
        return {"total": len(self._intents), "buy": buys, "sell": sells, "instruments": instruments}

    def report(self) -> ExecutionReport:
        """Simulated-mode fills/positions/PnL report (raises for native mode)."""
        if self._mode != "simulated":
            raise RuntimeError("report() is simulated-mode only; native uses close()/last_result")
        return self._simulator.report()

    # -- finalize ------------------------------------------------------------

    def _collect_fills(self):
        """Return ``(fills, engine_summary)`` from the configured fill source."""
        if self._mode == "simulated":
            return list(self._simulator.report().fills), None
        # native: run the real Nautilus engine (lazy import keeps this module clean)
        from strategy_framework.backends.nautilus_native import run_native_backtest

        return run_native_backtest(
            bars=self._bar_rows,
            intents_by_ts=self._intents_by_ts,
            instrument_id=self._instrument_id,
            quantity=self._quantity,
            initial_cash=self._initial_cash,
            allow_short=self._allow_short,
            fee_rate=self._fee_rate,
            slippage_bps=self._slippage_bps,
        )

    def close(self) -> None:
        fills, engine_summary = self._collect_fills()

        if self._output_dir is not None:
            from strategy_framework.execution.backtest_report import write_backtest_report

            result = write_backtest_report(
                output_dir=self._output_dir,
                run_name=self._run_name,
                mode=self._mode,
                backend="nautilus_backtest",
                initial_cash=self._initial_cash,
                bars=self._bar_rows,
                signals=self._signal_rows,
                intents=self._intent_rows,
                fills=fills,
                feature_names=self._spec_names,
                fee_rate=self._fee_rate,
                slippage_bps=self._slippage_bps,
                config=self._config,
                engine_summary=engine_summary,
            )
            self.last_result = result
            m = result.metrics
            print(f"[nautilus_backtest] mode={self._mode} run={self._run_name}")
            print(f"  output: {result.output_dir}")
            print(
                f"  metrics: final_equity={m['final_equity']:.2f} "
                f"total_return={m['total_return']:.4%} trades={m['trade_count']} "
                f"fills={m['fill_count']}"
            )
            return

        # No output directory configured: print a concise summary only.
        instruments = sorted({i.instrument_id for i in self._intents if i.instrument_id})
        print(f"[nautilus_backtest] mode={self._mode} (no output dir configured)")
        print(f"  intents: total={len(self._intents)} instruments={instruments}")
        print(f"  fills:   total={len(fills)}")
        if self._mode == "simulated" and self._simulator is not None:
            rep = self._simulator.report()
            print(f"  pnl:     realized={rep.realized_pnl:.4f} unrealized={rep.unrealized_pnl:.4f}")
