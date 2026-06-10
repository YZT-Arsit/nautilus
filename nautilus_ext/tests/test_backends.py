"""Tests for the execution-backend skeleton and the Nautilus-independence
boundaries (data_engine, feature compute).

These protect the ownership decision in
``docs/NAUTILUS_INTEGRATION_BOUNDARY.md``: our custom framework stays
independent of Nautilus Trader native objects, and the Nautilus backends remain
importable placeholders (no exchange / no heavy imports required).
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

from strategy_framework.backends import ExecutionBackend, build_backend
from strategy_framework.backends.paper import PaperBackend
from strategy_framework.backends.simple_backtest import SimpleBacktestBackend

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeEvent:
    def __init__(self, signal_price=100.0):
        self.instrument_id = "BTC/USDT"
        self.close = signal_price
        self.event_time_ns = 42


class _FakeSnapshot:
    def value(self, name):  # SignalRecorder reads snapshot.value(name)
        return 1.0


# A. Functional backends -----------------------------------------------------

class TestFunctionalBackends:

    def test_simple_backtest_records_and_satisfies_protocol(self, capsys):
        backend = SimpleBacktestBackend(["ma5_close"])
        assert isinstance(backend, ExecutionBackend)
        backend.on_signal(_FakeEvent(), _FakeSnapshot(), "BUY")
        backend.on_signal(_FakeEvent(), _FakeSnapshot(), "HOLD")
        assert backend.signal_counts() == {"BUY": 1, "HOLD": 1}
        backend.close()
        assert "simple_backtest" in capsys.readouterr().out

    def test_paper_logs_actionable_intents_only(self, capsys):
        backend = PaperBackend(["ma5_close"])
        assert isinstance(backend, ExecutionBackend)
        backend.on_signal(_FakeEvent(), _FakeSnapshot(), "BUY")
        backend.on_signal(_FakeEvent(), _FakeSnapshot(), "HOLD")  # ignored
        backend.on_signal(_FakeEvent(), _FakeSnapshot(), "SELL")
        assert [i["side"] for i in backend.intents()] == ["BUY", "SELL"]
        backend.close()
        out = capsys.readouterr().out
        assert "[paper] intent: BUY" in out
        assert "2 intended order(s)" in out


# B. build_backend factory ---------------------------------------------------

class TestBuildBackend:

    def test_no_execution_block_returns_none(self):
        assert build_backend({}, ["x"]) is None
        assert build_backend(None, ["x"]) is None
        assert build_backend({"foo": "bar"}, ["x"]) is None  # no 'backend' key

    @pytest.mark.parametrize("name,cls", [
        ("signal_recorder", SimpleBacktestBackend),
        ("simple_backtest", SimpleBacktestBackend),
        ("paper", PaperBackend),
    ])
    def test_known_backends(self, name, cls):
        assert isinstance(build_backend({"backend": name}, ["x"]), cls)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="unknown execution backend"):
            build_backend({"backend": "definitely_not_real"}, ["x"])


# C. Nautilus placeholders import cleanly (no exchange / no heavy imports) ----

class TestNautilusPlaceholders:

    def test_modules_import_without_nautilus(self):
        nb = importlib.import_module("strategy_framework.backends.nautilus_backtest")
        nl = importlib.import_module("strategy_framework.backends.nautilus_live")
        # Construct cheaply (no engine, no connection)...
        for mod, attr in ((nb, "NautilusBacktestBackend"), (nl, "NautilusLiveBackend")):
            backend = getattr(mod, attr)(["x"])
            assert isinstance(backend, ExecutionBackend)
            # ...but driving them is not implemented yet.
            with pytest.raises(NotImplementedError):
                backend.on_signal(_FakeEvent(), _FakeSnapshot(), "BUY")

    def test_build_backend_constructs_nautilus_placeholders(self):
        for name in ("nautilus_backtest", "nautilus_live"):
            backend = build_backend({"backend": name}, ["x"])
            assert isinstance(backend, ExecutionBackend)

    def test_placeholder_source_has_no_top_level_nautilus_import(self):
        import inspect

        import strategy_framework.backends.nautilus_backtest as nb
        import strategy_framework.backends.nautilus_live as nl

        for mod in (nb, nl):
            src = inspect.getsource(mod)
            # No eager Nautilus import at module scope (only mentioned in docs/TODOs).
            assert "import nautilus_trader" not in src
            assert "from nautilus_trader" not in src


# D. data_engine independence ------------------------------------------------

class TestDataEngineIndependence:

    def test_no_nautilus_or_pandas_imports(self):
        import inspect

        import data_engine

        pkg_dir = Path(data_engine.__file__).resolve().parent
        for mod in pkgutil.walk_packages([str(pkg_dir)], prefix="data_engine."):
            module = importlib.import_module(mod.name)
            src = inspect.getsource(module)
            assert "import pandas" not in src, mod.name
            assert "nautilus_trader" not in src, mod.name


# E. feature compute independence --------------------------------------------

class TestComputeIndependence:

    def test_compute_modules_do_not_import_nautilus_native(self):
        import inspect

        import nautilus_ext.features.compute as compute

        pkg_dir = Path(compute.__file__).resolve().parent
        for mod in pkgutil.walk_packages([str(pkg_dir)], prefix="nautilus_ext.features.compute."):
            module = importlib.import_module(mod.name)
            src = inspect.getsource(module)
            assert "import nautilus_trader" not in src, mod.name
            assert "from nautilus_trader" not in src, mod.name
