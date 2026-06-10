"""Boundary tests for the top-level strategy structure.

Protects the post-refactor layout:

* entry point   -> top-level ``run_strategy.py``
* strategies    -> ``strategies/<name>/`` (strategy.py + config.yaml + PLUGIN)
* framework glue -> ``strategy_framework/``
* compute engine -> ``nautilus_ext/features/compute/`` (unchanged)
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import run_strategy
from strategy_framework.registry import STRATEGY_REGISTRY, get_entry
from strategy_framework.plugin import StrategyPlugin

REPO_ROOT = Path(__file__).resolve().parents[2]
MA_CONFIG = REPO_ROOT / "strategies" / "ma_crossover" / "config.yaml"


# ===========================================================================
# A. Top-level runner
# ===========================================================================

class TestTopLevelRunner:

    def test_runs_with_strategy_flag(self, capsys):
        run_strategy.main(["--strategy", "ma_crossover"])
        assert "[ma_crossover] warmed up" in capsys.readouterr().out

    def test_runs_with_config_flag(self, capsys):
        run_strategy.main(["--config", str(MA_CONFIG)])
        assert "[ma_crossover] warmed up" in capsys.readouterr().out

    def test_strategy_overrides_config_strategy_key(self, tmp_path, capsys):
        # Config names a bogus strategy; --strategy must win.
        cfg = tmp_path / "override.yaml"
        cfg.write_text(
            "strategy: not_a_real_strategy\n"
            "params: {fast_window: 5, slow_window: 20}\n"
            "data: {mode: synthetic, warmup_bars: 20, live_bars: 20}\n"
            "output: {print_table: false}\n"
        )
        run_strategy.main(["--config", str(cfg), "--strategy", "ma_crossover"])
        assert "[ma_crossover] warmed up" in capsys.readouterr().out

    def test_no_args_errors(self):
        with pytest.raises(SystemExit):
            run_strategy.main([])

    def test_runner_lives_at_repo_root(self):
        assert (REPO_ROOT / "run_strategy.py").exists()


# ===========================================================================
# B. Strategy plugin
# ===========================================================================

class TestStrategyPlugin:

    def test_plugin_exists_and_fields(self):
        from strategies.ma_crossover import PLUGIN

        assert isinstance(PLUGIN, StrategyPlugin)
        assert PLUGIN.name == "ma_crossover"
        assert PLUGIN.default_config_path == "strategies/ma_crossover/config.yaml"

    def test_plugin_components_usable(self):
        from strategies.ma_crossover import PLUGIN

        config = PLUGIN.config_cls()
        specs = PLUGIN.build_specs(config)
        assert len(specs) == 2
        strategy = PLUGIN.strategy_cls(config)
        assert hasattr(strategy, "on_snapshot")

    def test_default_config_path_resolves(self):
        from strategies.ma_crossover import PLUGIN

        assert (REPO_ROOT / PLUGIN.default_config_path).exists()


# ===========================================================================
# C. Registry
# ===========================================================================

class TestRegistryStructure:

    def test_registry_contains_ma_crossover(self):
        assert "ma_crossover" in STRATEGY_REGISTRY

    def test_get_entry_returns_plugin(self):
        from strategies.ma_crossover import PLUGIN

        assert get_entry("ma_crossover") is PLUGIN

    def test_unknown_strategy_lists_valid(self):
        with pytest.raises(KeyError) as exc:
            get_entry("nope")
        assert "ma_crossover" in str(exc.value)


# ===========================================================================
# D. Strategy boundary
# ===========================================================================

class TestStrategyBoundary:

    FORBIDDEN = (
        "nautilus_ext.features.compute.features",
        "nautilus_ext.features.compute.backend",
        "nautilus_ext.features.compute.state",
        "nautilus_ext.features.compute.engine",
    )

    def test_strategy_no_compute_internal_imports(self):
        import strategies.ma_crossover.strategy as strat

        src = inspect.getsource(strat)
        for forbidden in self.FORBIDDEN:
            assert forbidden not in src
        assert "from nautilus_ext.features.api import" in src
        assert "from strategy_framework.plugin import StrategyPlugin" in src


# ===========================================================================
# E. Top-level runner boundary (coordination only)
# ===========================================================================

class TestRunnerBoundary:

    def test_delegates_to_framework(self):
        src = inspect.getsource(run_strategy)
        assert "from market_data_engine.loader import load_events" in src
        assert "from strategy_framework import output" in src
        assert "load_events(" in src
        assert "output." in src

    def test_no_inline_data_or_format_logic(self):
        src = inspect.getsource(run_strategy)
        assert "make_bars" not in src
        assert "live_closes" not in src
        assert "110.0" not in src
        assert "csv" not in src


# ===========================================================================
# F. Backward-compatibility wrapper + removed legacy package
# ===========================================================================

class TestCompatibilityShims:

    def test_scripts_wrapper_forwards(self):
        import scripts.run_ma_crossover_demo as legacy

        assert legacy.main is run_strategy.main

    def test_legacy_feature_strategies_package_removed(self):
        # The old parallel framework was fully removed; nothing should import it.
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("feature_strategies")
