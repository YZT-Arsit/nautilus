"""Tests for the Nautilus backtest backend + the shared report/metrics layer.

These run WITHOUT ``nautilus_trader`` installed (the native engine is exercised
only by the ``importorskip``-guarded smoke test). They cover:

* the dependency-free analytics/report writer (equity, trades, metrics, files);
* the backend in ``mode="simulated"`` producing the full artifact set;
* the intent mapping the native replay consumes;
* the native path raising a CLEAR error (not the old placeholder) when Nautilus
  is absent, and importing lazily;
* architectural boundaries: the execution + strategy layers never import Nautilus,
  and feature registration still flows through FeatureSpec.
"""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest

from strategy_framework.execution.reports import FillRecord
from strategy_framework.execution.backtest_report import write_backtest_report
from strategy_framework.backends.nautilus_backtest import (
    NautilusBacktestBackend,
    _intent_action,
)
from strategy_framework.execution.intents import OrderIntent, PositionIntent

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Snap:
    def __init__(self, values):
        self._v = values

    def value(self, name, default=None):
        return self._v.get(name, default)


class _Bar:
    def __init__(self, ts, close, instrument_id="BTCUSDT.BINANCE"):
        self.event_time_ns = ts
        self.instrument_id = instrument_id
        self.open = self.high = self.low = self.close = close
        self.volume = 1.0


# ===========================================================================
# A. Report/metrics layer (pure function, no backend)
# ===========================================================================

class TestReportLayer:

    def _bars(self):
        return [
            {"event_time_ns": i, "instrument_id": "X", "open": p, "high": p,
             "low": p, "close": p, "volume": 1.0}
            for i, p in enumerate([100.0, 110.0, 120.0, 90.0])
        ]

    def test_round_trip_metrics_and_files(self, tmp_path: Path):
        # BUY 1 @100 (ts0), SELL 1 @120 (ts2): +20 realized, win.
        fills = [
            FillRecord("X", "BUY", 1.0, 100.0, 0, "simulated", {}),
            FillRecord("X", "SELL", 1.0, 120.0, 2, "simulated", {}),
        ]
        result = write_backtest_report(
            output_dir=tmp_path / "run",
            run_name="run",
            mode="simulated",
            backend="nautilus_backtest",
            initial_cash=1000.0,
            bars=self._bars(),
            signals=[{"event_time_ns": 0, "instrument_id": "X", "signal": "BUY", "close": 100.0}],
            intents=[{"event_time_ns": 0, "instrument_id": "X", "action": "BUY",
                      "quantity": 1.0, "reason": "x"}],
            fills=fills,
        )
        m = result.metrics
        assert m["trade_count"] == 1
        assert m["win_rate"] == 1.0
        assert m["realized_pnl"] == pytest.approx(20.0)
        assert m["final_equity"] == pytest.approx(1020.0)
        assert m["total_return"] == pytest.approx(0.02)
        assert m["fill_count"] == 2
        assert m["bar_count"] == 4
        # every required artifact exists
        for fname in ("metrics.json", "report.md", "signals.csv", "intents.csv",
                      "trades.csv", "positions.csv", "equity_curve.csv", "fills.csv"):
            assert (tmp_path / "run" / fname).exists(), fname
        # metrics.json round-trips
        loaded = json.loads((tmp_path / "run" / "metrics.json").read_text())
        assert loaded["trade_count"] == 1

    def test_required_metric_keys_present(self, tmp_path: Path):
        result = write_backtest_report(
            output_dir=tmp_path / "r", run_name="r", mode="simulated",
            backend="nautilus_backtest", initial_cash=1000.0, bars=self._bars(),
            signals=[], intents=[], fills=[],
        )
        for key in ("total_return", "max_drawdown", "trade_count", "win_rate",
                    "final_equity", "initial_cash", "start_time", "end_time",
                    "bar_count", "signal_count"):
            assert key in result.metrics, key

    def test_max_drawdown_on_open_position(self, tmp_path: Path):
        # Buy and hold through a dip: equity should draw down, MTM unrealized < 0.
        fills = [FillRecord("X", "BUY", 1.0, 100.0, 0, "simulated", {})]
        result = write_backtest_report(
            output_dir=tmp_path / "r", run_name="r", mode="simulated",
            backend="nautilus_backtest", initial_cash=1000.0, bars=self._bars(),
            signals=[], intents=[], fills=fills,
        )
        assert result.metrics["max_drawdown"] > 0
        assert result.final_positions and result.final_positions[0]["quantity"] == 1.0


# ===========================================================================
# B. Backend in simulated mode (no Nautilus)
# ===========================================================================

class TestSimulatedBackend:

    def _drive(self, tmp_path, mode="simulated", extra=None):
        ctx = {
            "run_name": "ut",
            "output": {"root": str(tmp_path)},
            "data": {"instrument_id": "BTCUSDT.BINANCE"},
            "config": {"strategy": "ma_crossover"},
            "repo_root": str(tmp_path),
        }
        exe = {"backend": "nautilus_backtest", "mode": mode, "initial_cash": 1000.0,
               "quantity": 1.0, "sell_means": "flat", "price_field": "close"}
        if extra:
            exe.update(extra)
        be = NautilusBacktestBackend(["ma5_close"], exe, ctx)
        snap = _Snap({"ma5_close": 1.0})
        be.on_signal(_Bar(0, 100.0), snap, "BUY")
        be.on_signal(_Bar(1, 110.0), snap, "HOLD")
        be.on_signal(_Bar(2, 120.0), snap, "SELL")
        be.close()
        return be

    def test_simulated_writes_report_and_metrics(self, tmp_path: Path):
        be = self._drive(tmp_path)
        out = tmp_path / "ut"
        assert (out / "metrics.json").exists()
        assert (out / "report.md").exists()
        assert be.last_result is not None
        m = be.last_result.metrics
        assert m["mode"] == "simulated"
        assert m["trade_count"] == 1
        assert m["realized_pnl"] == pytest.approx(20.0)  # 100 -> 120

    def test_no_output_dir_does_not_write(self, capsys):
        be = NautilusBacktestBackend(["ma5_close"], {"backend": "nautilus_backtest"})
        be.on_signal(_Bar(0, 100.0), _Snap({"ma5_close": 1.0}), "BUY")
        be.close()  # no context -> summary only, no crash
        assert be.last_result is None
        assert "nautilus_backtest" in capsys.readouterr().out


# ===========================================================================
# C. Intent mapping consumed by the native replay
# ===========================================================================

class TestIntentMapping:

    def test_buy_order_intent(self):
        assert _intent_action(OrderIntent("X", "BUY", 2.0, 0)) == ("BUY", 2.0)

    def test_sell_order_intent(self):
        assert _intent_action(OrderIntent("X", "SELL", 1.5, 0)) == ("SELL", 1.5)

    def test_flat_position_intent(self):
        assert _intent_action(PositionIntent("X", "FLAT", 0.0, 0)) == ("FLAT", 0.0)


# ===========================================================================
# D. Native path: clear error (not placeholder) + lazy import
# ===========================================================================

class TestNativePathGuards:

    def test_native_module_imports_without_nautilus(self):
        mod = importlib.import_module("strategy_framework.backends.nautilus_native")
        assert hasattr(mod, "run_native_backtest")

    def test_native_close_raises_clear_error_when_nautilus_absent(self):
        import importlib.util

        from strategy_framework.backends.nautilus_native import NautilusUnavailableError

        # Probe availability WITHOUT importing nautilus_trader (find_spec does not
        # execute the module), so this test never pollutes sys.modules for the
        # "construction does not import nautilus" boundary tests.
        if importlib.util.find_spec("nautilus_trader") is not None:
            pytest.skip("nautilus_trader is installed; covered by the native smoke test")

        be = NautilusBacktestBackend(
            ["ma5_close"],
            {"backend": "nautilus_backtest", "mode": "nautilus_native"},
            {"data": {"instrument_id": "BTCUSDT.BINANCE"}},
        )
        be.on_signal(_Bar(0, 100.0), _Snap({"ma5_close": 1.0}), "BUY")
        # Must NOT be the old placeholder NotImplementedError.
        with pytest.raises(NautilusUnavailableError):
            be.close()

    def test_native_module_has_no_top_level_nautilus_import(self):
        import strategy_framework.backends.nautilus_native as mod

        for line in inspect.getsource(mod).splitlines():
            assert not line.startswith(("import nautilus_trader", "from nautilus_trader"))


# ===========================================================================
# E. Boundaries + feature registration untouched
# ===========================================================================

class TestBoundaries:

    def test_execution_layer_has_no_nautilus_imports(self):
        from strategy_framework.execution import (
            backtest_report,
            intents,
            reports,
            signal_policy,
        )

        for mod in (intents, reports, signal_policy, backtest_report):
            assert "nautilus_trader" not in inspect.getsource(mod), mod.__name__

    def test_strategies_do_not_import_nautilus(self):
        import strategies.ma_crossover.strategy as strat

        assert "nautilus_trader" not in inspect.getsource(strat)

    def test_feature_registration_still_uses_featurespec(self):
        # The plugin's build_specs returns FeatureSpec objects routed through the
        # FeatureSpec / BackendRegistry / PythonBackend path - unchanged here.
        from feature_engine.api import FeatureSpec
        from strategies.ma_crossover import MovingAverageCrossoverConfig, build_specs

        specs = build_specs(MovingAverageCrossoverConfig())
        assert specs and all(isinstance(s, FeatureSpec) for s in specs)
