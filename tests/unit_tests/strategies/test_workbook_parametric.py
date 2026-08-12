from pathlib import Path

import yaml

from feature_engine.api import FeatureSnapshot, FeatureValue
from strategies.workbook_parametric.config import WorkbookParametricConfig
from strategies.workbook_parametric.plugin import DEFINITIONS, PLUGINS, build_specs
from strategies.workbook_parametric.strategy import WorkbookParametricStrategy
from strategy_framework.registry import get_entry


def snapshot(**values: float) -> FeatureSnapshot:
    return FeatureSnapshot(
        ts_event=1,
        instrument_id="BTCUSDT-PERP.BINANCE",
        values={name: FeatureValue(name, value, True, source_event_time_ns=1) for name, value in values.items()},
    )


def test_reviewed_workbook_plugins_use_normal_registry_and_configs() -> None:
    assert {plugin.name for plugin in PLUGINS} == set(DEFINITIONS)
    for registry_id, config_path in DEFINITIONS.items():
        plugin = get_entry(registry_id)
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        config = plugin.config_cls(**payload["params"])
        assert config.source_registry_id == registry_id
        assert plugin.build_specs(config) == build_specs(config)


def test_sma_crossover_signal_uses_only_successive_ready_snapshots() -> None:
    strategy = WorkbookParametricStrategy(
        WorkbookParametricConfig(source_registry_id="xlsx_s1_0002", fast_window=20, slow_window=60)
    )
    assert strategy.on_snapshot(snapshot(workbook_close=100, workbook_fast=99, workbook_slow=100)) == "HOLD"
    assert strategy.on_snapshot(snapshot(workbook_close=101, workbook_fast=101, workbook_slow=100)) == "BUY"
    assert strategy.decision_position == 1
    assert strategy.on_snapshot(snapshot(workbook_close=99, workbook_fast=99, workbook_slow=100)) == "SELL"
    assert strategy.decision_position == -1


def test_ma_envelope_exit_sets_decision_target_flat_without_assuming_fill() -> None:
    strategy = WorkbookParametricStrategy(
        WorkbookParametricConfig(source_registry_id="xlsx_s1_0005", family="ma_envelope")
    )
    strategy.on_snapshot(snapshot(workbook_close=100, workbook_middle=100))
    assert strategy.on_snapshot(snapshot(workbook_close=103, workbook_middle=100)) == "BUY"
    assert strategy.on_snapshot(snapshot(workbook_close=99, workbook_middle=100)) == "HOLD"
    assert strategy.decision_position == 0
