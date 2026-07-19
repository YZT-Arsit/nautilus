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

from dataclasses import replace
from pathlib import Path
from typing import Any

from strategy_framework.execution.intents import OrderIntent, PositionIntent
from strategy_framework.execution.reports import ExecutionReport
from strategy_framework.execution.signal_policy import SignalToOrderPolicy, plan_to_intents

_SUPPORTED_MODES = ("simulated", "nautilus_native")


def _fee_label(fee: float) -> str:
    if float(fee) == 0.0:
        return "nofee"
    bps = float(fee) * 10_000
    return f"fee_{int(bps)}bps" if bps == int(bps) else f"fee_{bps:g}bps".replace(".", "p")


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


def _shift_intents_by_bars(
    intents_by_ts: dict[int, tuple[str, float]],
    ordered_ts: list[int],
    latency_bars: int = 1,
) -> tuple[dict[int, tuple[str, float]], int]:
    """Shift each intent's execution timestamp by ``latency_bars`` bars.

    ``ordered_ts`` is the full, ascending, de-duplicated list of bar timestamps.
    An intent keyed at ``ts[t]`` moves to ``ts[t+1]``; an intent on the final bar
    has no next bar and is **dropped** (and counted). Returns
    ``(shifted_map, dropped_count)``.

    The signal is still computed on bar ``t`` upstream - only *execution* moves to
    ``t+1`` - so this removes the same-bar close-to-fill optimism without any
    strategy-side change. The mapping ``ts[t] -> ts[t+1]`` is injective over a
    strictly increasing sequence, so distinct intents never collide after the
    shift (no silent loss): every non-tail intent is preserved.
    """
    if latency_bars < 0:
        raise ValueError("latency_bars must be non-negative")
    next_of: dict[int, int] = {}
    for i in range(len(ordered_ts) - latency_bars):
        next_of[ordered_ts[i]] = ordered_ts[i + latency_bars]
    shifted: dict[int, tuple[str, float]] = {}
    dropped = 0
    for ts, action in intents_by_ts.items():
        nxt = next_of.get(int(ts))
        if nxt is None:
            dropped += 1
        else:
            shifted[nxt] = action
    return shifted, dropped


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
        self._fee_scenarios = [float(value) for value in cfg.get("fee_scenarios", [])]
        if len(self._fee_scenarios) > 1 and self._mode != "simulated":
            raise ValueError("single-pass fee_scenarios currently require mode='simulated'")
        self._slippage_bps = float(cfg.get("slippage_bps", 0.0))

        # Execution timing: same_bar (default, legacy) submits/fills on the signal
        # bar; next_bar shifts execution to the following bar to remove the
        # same-bar close-to-fill optimism. Strategy-agnostic; the strategy never
        # sees it. next_bar is wired for the native engine path only in this step.
        self._fill_timing = cfg.get("fill_timing", "same_bar")
        if self._fill_timing not in ("same_bar", "next_bar"):
            raise ValueError(
                f"unknown fill_timing {self._fill_timing!r}. Supported: "
                "('same_bar', 'next_bar')"
            )
        default_latency = 1 if self._fill_timing == "next_bar" else 0
        self._latency_bars = int(cfg.get("latency_bars", default_latency))
        if self._latency_bars < 0:
            raise ValueError("latency_bars must be non-negative")
        if self._fill_timing == "same_bar" and self._latency_bars:
            raise ValueError("same_bar execution requires latency_bars=0")
        # Populated at close(): the (possibly shifted) execution map + its stats.
        self._exec_map: dict[int, tuple[str, float]] | None = None
        self._exec_stats: dict[str, Any] | None = None
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
        self._pending_simulated: list[tuple[int, Any]] = []
        self._bar_index = -1
        self._dropped_pending = 0

        # Run/output context (provided by run_strategy; may be empty for unit use).
        data_cfg = ctx.get("data", {}) or {}
        self._instrument_id = data_cfg.get("instrument_id", "BTCUSDT.BINANCE")
        self._run_name = ctx.get("run_name") or cfg.get("run_name") or "backtest"
        self._config = ctx.get("config")
        self._output_dir = self._resolve_output_dir(ctx)
        self._funding_events = list(ctx.get("funding_events") or [])
        self.last_result = None  # populated by close() when a report is written
        self.last_results = []

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
        self._bar_index += 1
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
        if self._mode == "simulated" and self._latency_bars:
            self._execute_due_simulated(event)
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

        # Rich-plan strategies (sized / pyramiding, e.g. the Turtle system) return
        # a PlannedSignal carrying an explicit list of sized TradeActions. When
        # present, execute those directly (sizing already decided by the strategy)
        # instead of routing a single fixed-quantity order through the policy.
        actions = getattr(signal, "actions", None)
        if actions is not None:
            for intent in plan_to_intents(actions, event):
                self._process_intent(intent, ts, event)
            return

        intent = self._policy.on_signal(event, snapshot, signal)
        if intent is None:
            return
        self._process_intent(intent, ts, event)

    def _process_intent(self, intent: Any, ts: int | None, event: Any) -> None:
        """Record one intent and route it to the configured fill source."""
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
            if self._latency_bars:
                self._pending_simulated.append((self._bar_index + self._latency_bars, intent))
            else:
                self._simulator.on_intent(intent, event)
        elif self._mode == "nautilus_native":
            if ts is None or action is None:
                return
            if int(ts) in self._intents_by_ts:
                # One (side, quantity) slot per bar timestamp: the native replay
                # cannot yet execute multiple sized orders on one bar (pyramiding).
                raise NotImplementedError(
                    "mode='nautilus_native' cannot replay multiple orders on one "
                    "bar (pyramiding/rich plans). Use mode='simulated' for sized "
                    "multi-order strategies like the Turtle system; native "
                    "multi-order replay is a follow-up."
                )
            self._intents_by_ts[int(ts)] = action

    def _execute_due_simulated(self, event: Any) -> None:
        """Fill due intents at the delayed bar open with execution timestamp."""
        due = [item for item in self._pending_simulated if item[0] <= self._bar_index]
        self._pending_simulated = [item for item in self._pending_simulated if item[0] > self._bar_index]
        execution_ts = int(getattr(event, "event_time_ns", 0))
        execution_price = float(getattr(event, "open", getattr(event, "close")))
        for _, intent in due:
            metadata = {**(getattr(intent, "metadata", {}) or {})}
            metadata["signal_time_ns"] = int(getattr(intent, "event_time_ns", 0))
            metadata["fill_price"] = execution_price
            delayed = replace(intent, event_time_ns=execution_ts, metadata=metadata)
            self._simulator.on_intent(delayed, event)

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

    def _execution_intents(self) -> tuple[dict[int, tuple[str, float]], dict[str, Any]]:
        """Resolve the execution intent map honoring ``fill_timing``.

        ``same_bar`` (default) returns the intents unchanged (legacy behaviour).
        ``next_bar`` shifts each intent to the following bar; the final bar's
        intent has no next bar and is dropped (counted). Returns ``(map, stats)``
        where ``stats`` carries ``fill_timing`` + original/executed/dropped counts
        for the report. Pure: no Nautilus, safe to call without the engine.
        """
        original = len(self._intents_by_ts)
        if self._fill_timing == "same_bar":
            return dict(self._intents_by_ts), {
                "fill_timing": "same_bar",
                "original_intent_count": original,
                "executed_intent_count": original,
                "dropped_tail_intents": 0,
            }
        ordered_ts = sorted({
            int(b["event_time_ns"]) for b in self._bar_rows
            if b.get("event_time_ns") is not None
        })
        shifted, dropped = _shift_intents_by_bars(
            self._intents_by_ts, ordered_ts, self._latency_bars,
        )
        return shifted, {
            "fill_timing": "next_bar",
            "original_intent_count": original,
            "executed_intent_count": len(shifted),
            "dropped_tail_intents": dropped,
            "latency_bars": self._latency_bars,
        }

    def _collect_fills(self):
        """Return ``(fills, engine_summary)`` from the configured fill source."""
        if self._mode == "simulated":
            self._dropped_pending = len(self._pending_simulated)
            fills = list(self._simulator.report().fills)
            from strategy_framework.execution.costs import apply_adverse_slippage
            return [apply_adverse_slippage(fill, self._slippage_bps) for fill in fills], None
        # native: run the real Nautilus engine (lazy import keeps this module clean)
        from strategy_framework.backends.nautilus_native import run_native_backtest

        # Use the fill_timing-resolved execution map (same_bar = identity).
        exec_map = self._exec_map if self._exec_map is not None else self._intents_by_ts
        fills, summary = run_native_backtest(
            bars=self._bar_rows,
            intents_by_ts=exec_map,
            instrument_id=self._instrument_id,
            quantity=self._quantity,
            initial_cash=self._initial_cash,
            allow_short=self._allow_short,
            fee_rate=self._fee_rate,
            slippage_bps=self._slippage_bps,
        )
        from strategy_framework.execution.costs import apply_adverse_slippage
        return [apply_adverse_slippage(fill, self._slippage_bps) for fill in fills], summary

    def close(self) -> None:
        # Resolve the execution intent map (same_bar = identity; next_bar shifts
        # execution to the following bar) BEFORE collecting fills, and keep its
        # stats for the report.
        self._exec_map, self._exec_stats = self._execution_intents()
        if self._mode == "simulated":
            self._exec_stats["executed_intent_count"] = len(self._simulator.report().fills)
            self._exec_stats["dropped_tail_intents"] = len(self._pending_simulated)
            self._exec_stats["latency_bars"] = self._latency_bars
        fills, engine_summary = self._collect_fills()

        if self._output_dir is not None:
            from strategy_framework.execution.backtest_report import write_backtest_report
            scenarios = self._fee_scenarios or [self._fee_rate]
            multi = len(scenarios) > 1
            for fee in scenarios:
                run_name = f"{self._run_name}/{_fee_label(fee)}" if multi else self._run_name
                output_dir = self._output_dir / _fee_label(fee) if multi else self._output_dir
                result = write_backtest_report(
                    output_dir=output_dir,
                    run_name=run_name,
                    mode=self._mode,
                    backend="nautilus_backtest",
                    initial_cash=self._initial_cash,
                    bars=self._bar_rows,
                    signals=self._signal_rows,
                    intents=self._intent_rows,
                    fills=fills,
                    feature_names=self._spec_names,
                    fee_rate=fee,
                    slippage_bps=self._slippage_bps,
                    fill_timing=self._fill_timing,
                    execution_stats=self._exec_stats,
                    funding_events=self._funding_events,
                    config=self._config,
                    engine_summary=engine_summary,
                )
                self.last_result = result
                self.last_results.append(result)
                m = result.metrics
                print(f"[nautilus_backtest] mode={self._mode} run={run_name}")
                print(f"  output: {result.output_dir}")
                print(
                    f"  metrics: final_equity={m['final_equity']:.2f} "
                    f"total_return={m['total_return']:.4%} trades={m['trade_count']} "
                    f"fills={m['fill_count']}"
                )
            if multi:
                from results.charts import render_fee_compare
                render_fee_compare(self._output_dir)
                from strategy_framework.execution.backtest_report import write_artifact_manifest
                write_artifact_manifest(self._output_dir, self._run_name)
            return

        # No output directory configured: print a concise summary only.
        instruments = sorted({i.instrument_id for i in self._intents if i.instrument_id})
        print(f"[nautilus_backtest] mode={self._mode} fill_timing={self._fill_timing} "
              "(no output dir configured)")
        print(f"  intents: total={len(self._intents)} instruments={instruments}")
        print(f"  fills:   total={len(fills)}")
        if self._mode == "simulated" and self._simulator is not None:
            rep = self._simulator.report()
            print(f"  pnl:     realized={rep.realized_pnl:.4f} unrealized={rep.unrealized_pnl:.4f}")
