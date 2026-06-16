"""Tests for the execution-intent layer and the Nautilus backtest backend."""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_framework.backends.nautilus_backtest import (
    NautilusBacktestBackend,
    try_build_nautilus_backtest_engine,
    try_translate_to_nautilus_order,
)
from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.backends.paper import PaperBackend
from strategy_framework.execution import (
    ExecutionReport,
    FillRecord,
    OrderIntent,
    PositionIntent,
    PositionRecord,
    SignalToOrderPolicy,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Event:
    def __init__(self, instrument_id="BTC/USDT", close=100.0, event_time_ns=7):
        self.instrument_id = instrument_id
        self.close = close
        self.event_time_ns = event_time_ns


class _NoPriceEvent:
    def __init__(self, instrument_id="BTC/USDT", event_time_ns=7):
        self.instrument_id = instrument_id  # deliberately no 'close'
        self.event_time_ns = event_time_ns


class _Snapshot:
    def __init__(self, values=None):
        self._values = values or {}

    def value(self, name, default=None):
        return self._values.get(name, default)


# A. OrderIntent model + policy ----------------------------------------------

class TestSignalToOrderPolicy:

    def test_hold_returns_none(self):
        assert SignalToOrderPolicy().on_signal(_Event(), _Snapshot(), "HOLD") is None

    def test_buy_creates_buy_order_intent(self):
        intent = SignalToOrderPolicy(quantity=2.0).on_signal(_Event(), _Snapshot(), "BUY")
        assert isinstance(intent, OrderIntent)
        assert intent.side == "BUY"
        assert intent.quantity == 2.0
        assert intent.instrument_id == "BTC/USDT"
        assert intent.event_time_ns == 7

    def test_sell_flat_creates_position_intent(self):
        intent = SignalToOrderPolicy(sell_means="flat").on_signal(_Event(), _Snapshot(), "SELL")
        assert isinstance(intent, PositionIntent)
        assert intent.target == "FLAT"

    def test_sell_short_creates_sell_order_intent(self):
        intent = SignalToOrderPolicy(sell_means="short").on_signal(_Event(), _Snapshot(), "SELL")
        assert isinstance(intent, OrderIntent)
        assert intent.side == "SELL"

    def test_invalid_sell_means_raises(self):
        with pytest.raises(ValueError, match="sell_means"):
            SignalToOrderPolicy(sell_means="hedge")

    def test_metadata_includes_price_and_named_features(self):
        policy = SignalToOrderPolicy(spec_names=["ma5_close"])
        intent = policy.on_signal(_Event(close=110.0), _Snapshot({"ma5_close": 105.0}), "BUY")
        assert intent.metadata["price"] == 110.0
        assert intent.metadata["ma5_close"] == 105.0

    def test_intents_are_frozen(self):
        intent = SignalToOrderPolicy().on_signal(_Event(), _Snapshot(), "BUY")
        with pytest.raises(Exception):
            intent.quantity = 99.0  # frozen dataclass


# B. Paper backend uses the policy -------------------------------------------

class TestPaperBackend:

    def test_records_intents_and_skips_hold(self, capsys):
        backend = PaperBackend(["ma5_close"], {"quantity": 3.0})
        backend.on_signal(_Event(), _Snapshot(), "BUY")
        backend.on_signal(_Event(), _Snapshot(), "HOLD")  # ignored
        backend.on_signal(_Event(), _Snapshot(), "SELL")
        intents = backend.intents()
        assert len(intents) == 2
        assert isinstance(intents[0], OrderIntent) and intents[0].quantity == 3.0
        backend.close()  # must not raise
        assert "[paper]" in capsys.readouterr().out

    def test_sell_means_short_via_config(self):
        backend = PaperBackend(["x"], {"sell_means": "short"})
        backend.on_signal(_Event(), _Snapshot(), "SELL")
        assert backend.intents()[0].side == "SELL"


# C. NautilusBacktestBackend MVP ---------------------------------------------

class TestNautilusBacktestMVP:

    def test_construction_does_not_import_nautilus(self):
        import sys

        # constructing the backend must not pull nautilus_trader into sys.modules
        NautilusBacktestBackend(["x"], {"quantity": 1.0})
        assert "nautilus_trader" not in sys.modules

    def test_on_signal_records_buy_and_skips_hold(self):
        backend = NautilusBacktestBackend(["x"])
        backend.on_signal(_Event(), _Snapshot(), "BUY")
        backend.on_signal(_Event(), _Snapshot(), "HOLD")
        assert len(backend.intents()) == 1

    def test_summary_counts_and_close(self, capsys):
        backend = NautilusBacktestBackend(["x"], {"sell_means": "flat"})
        backend.on_signal(_Event(instrument_id="BTC/USDT"), _Snapshot(), "BUY")
        backend.on_signal(_Event(instrument_id="ETH/USDT"), _Snapshot(), "SELL")
        s = backend.summary()
        assert s == {"total": 2, "buy": 1, "sell": 1, "instruments": ["BTC/USDT", "ETH/USDT"]}
        backend.close()  # must not raise
        assert "[nautilus_backtest]" in capsys.readouterr().out

    def test_translate_returns_none_without_nautilus(self):
        intent = OrderIntent("BTC/USDT", "BUY", 1.0, 0)
        assert try_translate_to_nautilus_order(intent) is None


# D. Boundary tests ----------------------------------------------------------

class TestExecutionBoundaries:

    def test_strategy_does_not_import_execution_or_nautilus(self):
        src = (REPO_ROOT / "strategies" / "ma_crossover" / "strategy.py").read_text()
        assert "strategy_framework.execution" not in src
        assert "nautilus_trader" not in src

    def test_execution_layer_has_no_nautilus_import(self):
        for name in ("intents.py", "signal_policy.py", "__init__.py"):
            src = (REPO_ROOT / "strategy_framework" / "execution" / name).read_text()
            assert "nautilus_trader" not in src

    def test_feature_engine_has_no_execution_or_backtest_import(self):
        # feature_engine may use Nautilus *indicators*, but never its
        # execution/backtest/trading engine. Scan source text (no import needed).
        targets = ("nautilus_trader.backtest", "nautilus_trader.execution", "nautilus_trader.trading")
        for path in (REPO_ROOT / "feature_engine").rglob("*.py"):
            text = path.read_text()
            for t in targets:
                assert t not in text, f"{path} imports {t}"

    def test_data_engine_has_no_nautilus(self):
        for path in (REPO_ROOT / "data_engine").rglob("*.py"):
            assert "nautilus_trader" not in path.read_text(), path


# E. End-to-end via run_strategy ---------------------------------------------

class TestRunStrategyNautilusBacktest:

    def test_ma_crossover_with_nautilus_backtest_backend(self, tmp_path, capsys):
        import run_strategy

        cfg = tmp_path / "nb.yaml"
        cfg.write_text(
            "strategy: ma_crossover\n"
            "params: {fast_window: 5, slow_window: 20}\n"
            "data: {mode: synthetic, warmup_bars: 20, live_bars: 20}\n"
            "output: {print_table: false}\n"
            "execution: {backend: nautilus_backtest, mode: simulated, quantity: 1.0, sell_means: flat}\n"
        )
        run_strategy.main(["--config", str(cfg)])
        out = capsys.readouterr().out
        assert "[nautilus_backtest]" in out
        assert "intents:" in out
        assert "fills:" in out
        assert "pnl:" in out


# F. Report dataclasses ------------------------------------------------------

class TestReportModels:

    def test_fill_record_constructs(self):
        f = FillRecord("BTC/USDT", "BUY", 1.0, 100.0, 7)
        assert f.source == "simulated" and f.metadata == {}

    def test_position_record_constructs(self):
        p = PositionRecord("BTC/USDT", 1.0, 100.0, 110.0, 10.0)
        assert p.realized_pnl == 0.0

    def test_execution_report_constructs(self):
        r = ExecutionReport("nautilus_backtest", 2, 2, [], [], 5.0, 1.0)
        assert r.backend == "nautilus_backtest" and r.total_fills == 2


# G. IntentFillSimulator -----------------------------------------------------

class TestIntentFillSimulator:

    def _buy(self, instrument="BTC/USDT", qty=1.0):
        return OrderIntent(instrument, "BUY", qty, 1)

    def _sell(self, instrument="BTC/USDT", qty=1.0):
        return OrderIntent(instrument, "SELL", qty, 2)

    def test_buy_creates_fill_and_long_position(self):
        sim = IntentFillSimulator()
        fill = sim.on_intent(self._buy(), _Event(close=100.0))
        assert isinstance(fill, FillRecord) and fill.side == "BUY" and fill.price == 100.0
        rep = sim.report()
        assert rep.total_fills == 1
        assert rep.positions[0].quantity == 1.0 and rep.positions[0].avg_price == 100.0

    def test_sell_after_buy_realizes_pnl_and_closes(self):
        sim = IntentFillSimulator()
        sim.on_intent(self._buy(), _Event(close=100.0))
        sim.on_intent(self._sell(), _Event(close=110.0))
        rep = sim.report()
        assert rep.total_fills == 2
        assert rep.realized_pnl == pytest.approx(10.0)
        assert rep.positions == []  # flat

    def test_partial_sell_reduces_and_keeps_avg(self):
        sim = IntentFillSimulator()
        sim.on_intent(self._buy(qty=2.0), _Event(close=100.0))
        sim.on_intent(self._sell(qty=1.0), _Event(close=120.0))
        rep = sim.report()
        assert rep.realized_pnl == pytest.approx(20.0)
        assert rep.positions[0].quantity == 1.0 and rep.positions[0].avg_price == 100.0

    def test_position_intent_flat_after_buy_sells(self):
        sim = IntentFillSimulator()
        sim.on_intent(self._buy(qty=3.0), _Event(close=100.0))
        fill = sim.on_intent(PositionIntent("BTC/USDT", "FLAT", 0.0, 3), _Event(close=130.0))
        assert fill is not None and fill.side == "SELL" and fill.quantity == 3.0
        assert sim.report().positions == []

    def test_position_intent_flat_without_position_no_fill(self):
        sim = IntentFillSimulator()
        fill = sim.on_intent(PositionIntent("BTC/USDT", "FLAT", 0.0, 1), _Event())
        assert fill is None
        assert sim.report().total_fills == 0

    def test_missing_price_raises(self):
        sim = IntentFillSimulator()
        with pytest.raises(ValueError, match="no 'close'|no .price."):
            sim.on_intent(self._buy(), _NoPriceEvent())

    def test_missing_event_price_falls_back_to_metadata(self):
        sim = IntentFillSimulator()
        intent = OrderIntent("BTC/USDT", "BUY", 1.0, 1, metadata={"price": 99.0})
        fill = sim.on_intent(intent, _NoPriceEvent())
        assert fill.price == 99.0

    def test_allow_short_false_prevents_negative_position(self):
        sim = IntentFillSimulator(allow_short=False)
        fill = sim.on_intent(self._sell(), _Event(close=100.0))  # sell with no inventory
        assert fill is None
        # buy 1 then sell 2: only 1 can be sold; never negative
        sim.on_intent(self._buy(qty=1.0), _Event(close=100.0))
        sim.on_intent(self._sell(qty=2.0), _Event(close=100.0))
        for p in sim.report().positions:
            assert p.quantity >= 0

    def test_allow_short_true_opens_short(self):
        sim = IntentFillSimulator(allow_short=True)
        fill = sim.on_intent(self._sell(), _Event(close=100.0))
        assert fill is not None and fill.side == "SELL"
        assert sim.report().positions[0].quantity == -1.0


# H. NautilusBacktestBackend simulated report + native lazy mode -------------

class TestNautilusBacktestSimulated:

    def test_buy_then_sell_report_counts(self):
        backend = NautilusBacktestBackend(["x"], {"sell_means": "short"})
        backend.on_signal(_Event(close=100.0), _Snapshot(), "BUY")
        backend.on_signal(_Event(close=110.0), _Snapshot(), "SELL")
        rep = backend.report()
        assert rep.total_intents == 2 and rep.total_fills == 2
        assert rep.realized_pnl == pytest.approx(10.0)

    def test_close_prints_report(self, capsys):
        # No output dir configured -> concise summary (no report directory).
        backend = NautilusBacktestBackend(["x"])
        backend.on_signal(_Event(), _Snapshot(), "BUY")
        backend.close()
        out = capsys.readouterr().out
        assert "[nautilus_backtest]" in out and "fills:" in out

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="unknown nautilus_backtest mode"):
            NautilusBacktestBackend(["x"], {"mode": "warp_drive"})


class TestNautilusNativeModeLazy:

    def test_native_construct_does_not_import_nautilus(self):
        import sys

        NautilusBacktestBackend(["x"], {"mode": "nautilus_native"})
        assert "nautilus_trader" not in sys.modules

    def test_native_on_signal_does_not_raise(self):
        # Native mode accumulates during the run; the engine runs at close().
        # This must NOT raise the old placeholder NotImplementedError.
        backend = NautilusBacktestBackend(["x"], {"mode": "nautilus_native"})
        backend.on_signal(_Event(), _Snapshot(), "BUY")  # no raise

    def test_native_report_raises_runtime_error(self):
        # report() is simulated-mode only; native users read close()/last_result.
        backend = NautilusBacktestBackend(["x"], {"mode": "nautilus_native"})
        with pytest.raises(RuntimeError):
            backend.report()

    def test_native_close_clear_error_or_runs(self):
        import importlib.util

        from strategy_framework.backends.nautilus_native import NautilusUnavailableError

        # find_spec probes availability without importing nautilus_trader (so this
        # test does not pollute sys.modules for the construction boundary tests).
        if importlib.util.find_spec("nautilus_trader") is not None:
            pytest.skip("nautilus_trader present; native engine covered by smoke test")
        backend = NautilusBacktestBackend(["x"], {"mode": "nautilus_native"})
        backend.on_signal(_Event(), _Snapshot(), "BUY")
        # Clear dependency error, NOT a placeholder NotImplementedError.
        with pytest.raises(NautilusUnavailableError):
            backend.close()

    def test_build_engine_helper_returns_none_without_nautilus(self):
        assert try_build_nautilus_backtest_engine({}) is None
