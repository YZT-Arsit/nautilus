"""Tests for the execution-intent layer and the Nautilus backtest MVP backend."""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_framework.backends.nautilus_backtest import (
    NautilusBacktestBackend,
    try_translate_to_nautilus_order,
)
from strategy_framework.backends.paper import PaperBackend
from strategy_framework.execution import OrderIntent, PositionIntent, SignalToOrderPolicy

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Event:
    def __init__(self, instrument_id="BTC/USDT", close=100.0, event_time_ns=7):
        self.instrument_id = instrument_id
        self.close = close
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
            "execution: {backend: nautilus_backtest, quantity: 1.0, sell_means: flat}\n"
        )
        run_strategy.main(["--config", str(cfg)])
        out = capsys.readouterr().out
        assert "[nautilus_backtest] intents:" in out
        assert "BUY=1" in out and "SELL=1" in out
