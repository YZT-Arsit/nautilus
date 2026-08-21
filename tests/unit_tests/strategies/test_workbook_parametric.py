from pathlib import Path

import yaml

from data_engine.events import BarEvent
from feature_engine.api import FeatureSnapshot, FeatureValue
from feature_engine.runner import FeatureStrategyRunner
from scripts.internal.audit_strategy_workbook import IMPLEMENTED
from strategies.workbook_parametric.plugin import build_specs
from strategy_framework.execution.intents import PlannedSignal
from strategy_framework.execution.reports import ExecutionReport, FillRecord
from strategy_framework.registry import get_entry


def snapshot(**values: float) -> FeatureSnapshot:
    return FeatureSnapshot(
        ts_event=1,
        instrument_id="BTCUSDT-PERP.BINANCE",
        values={name: FeatureValue(name, value, True, source_event_time_ns=1) for name, value in values.items()},
    )


def test_every_reviewed_row_is_a_first_class_normal_package() -> None:
    required = {"__init__.py", "config.py", "strategy.py", "plugin.py", "config.yaml"}
    for registry_id in IMPLEMENTED:
        package = Path("strategies") / registry_id
        assert required <= {path.name for path in package.iterdir() if path.is_file()}
        plugin = get_entry(registry_id)
        assert plugin.name == registry_id
        assert Path(plugin.default_config_path) == package / "config.yaml"
        payload = yaml.safe_load(Path(plugin.default_config_path).read_text(encoding="utf-8"))
        config = plugin.config_cls(**payload["params"])
        assert config.source_registry_id == registry_id
        assert plugin.strategy_cls(config)
        assert plugin.build_specs(config) == build_specs(config)


def test_every_reviewed_row_consumes_normal_bar_stream_without_structural_error() -> None:
    events = [
        BarEvent(
            close=100.0 + index * 0.05 + (index % 7) * 0.1,
            open=100.0 + index * 0.05,
            high=101.0 + index * 0.05,
            low=99.0 + index * 0.05,
            volume=10.0 + index,
            instrument_id="BTCUSDT-PERP.BINANCE",
            event_time_ns=(index + 1) * 60_000_000_000,
        )
        for index in range(140)
    ]
    for registry_id in IMPLEMENTED:
        plugin = get_entry(registry_id)
        config = plugin.config_cls()
        runner = FeatureStrategyRunner(plugin.build_specs(config), plugin.strategy_cls(config))
        signals = [str(signal) for _, _, signal in runner.run(events)]
        assert len(signals) == len(events)
        assert set(signals) <= {"BUY", "SELL", "HOLD", "EXIT"}


def test_sma_crossover_golden_entry_and_reverse() -> None:
    plugin = get_entry("xlsx_s1_0002")
    strategy = plugin.strategy_cls(plugin.config_cls())
    assert strategy.on_snapshot(snapshot(workbook_close=100, workbook_fast=99, workbook_slow=100)) == "HOLD"
    buy = strategy.on_snapshot(snapshot(workbook_close=101, workbook_fast=101, workbook_slow=100))
    assert isinstance(buy, PlannedSignal) and buy == "BUY" and len(buy.actions) == 1
    reverse = strategy.on_snapshot(snapshot(workbook_close=99, workbook_fast=99, workbook_slow=100))
    assert reverse == "SELL" and len(reverse.actions) == 2
    assert strategy.decision_position == -1


def test_ma_envelope_golden_exit_is_explicit_flat_intent() -> None:
    plugin = get_entry("xlsx_s1_0005")
    strategy = plugin.strategy_cls(plugin.config_cls())
    strategy.on_snapshot(snapshot(workbook_close=100, workbook_middle=100))
    assert strategy.on_snapshot(snapshot(workbook_close=103, workbook_middle=100)) == "BUY"
    exit_signal = strategy.on_snapshot(snapshot(workbook_close=99, workbook_middle=100))
    assert exit_signal == "EXIT"
    assert exit_signal.actions[0].close_all is True
    assert strategy.decision_position == 0


def test_bollinger_and_atr_confirmed_need_exact_consecutive_bars() -> None:
    boll_plugin = get_entry("xlsx_s1_0033")
    boll = boll_plugin.strategy_cls(boll_plugin.config_cls())
    assert boll.on_snapshot(snapshot(workbook_close=101, workbook_middle=100, workbook_percent_b=1.1)) == "HOLD"
    assert boll.on_snapshot(snapshot(workbook_close=102, workbook_middle=100, workbook_percent_b=1.2)) == "BUY"
    atr_plugin = get_entry("xlsx_s1_0026")
    atr = atr_plugin.strategy_cls(atr_plugin.config_cls())
    row = dict(workbook_close=103, workbook_middle=100, workbook_atr=1)
    assert atr.on_snapshot(snapshot(**row)) == "HOLD"
    assert atr.on_snapshot(snapshot(**row)) == "BUY"


def test_phase2_2b_bollinger_width_source_exact_crosses_are_preserved() -> None:
    plugin = get_entry("xlsx_s1_0011")
    config = plugin.config_cls()
    assert config.semantic_provenance == "SOURCE_EXACT"
    assert config.defaulted_parameters == ""
    strategy = plugin.strategy_cls(config)
    assert strategy.on_snapshot(snapshot(
        workbook_close=100, workbook_bbw_fast=0.04, workbook_bbw_slow=0.05,
    )) == "HOLD"
    assert strategy.on_snapshot(snapshot(
        workbook_close=101, workbook_bbw_fast=0.06, workbook_bbw_slow=0.05,
    )) == "BUY"
    reverse = strategy.on_snapshot(snapshot(
        workbook_close=99, workbook_bbw_fast=0.03, workbook_bbw_slow=0.05,
    ))
    assert reverse == "SELL"
    assert len(reverse.actions) == 2


def test_phase2_2b_ma_slope_and_rsi_turn_contracts_have_golden_entries() -> None:
    ma_plugin = get_entry("xlsx_s2_0021")
    ma = ma_plugin.strategy_cls(ma_plugin.config_cls())
    assert ma.on_snapshot(snapshot(
        workbook_close=100, workbook_fast=99, workbook_slow=100, workbook_atr=2,
    )) == "HOLD"
    assert ma.on_snapshot(snapshot(
        workbook_close=102, workbook_fast=101, workbook_slow=100.5, workbook_atr=2,
    )) == "BUY"

    rsi_plugin = get_entry("xlsx_s2_0266")
    rsi = rsi_plugin.strategy_cls(rsi_plugin.config_cls())
    assert rsi.on_snapshot(snapshot(workbook_close=100, workbook_open=101, workbook_rsi=40)) == "HOLD"
    assert rsi.on_snapshot(snapshot(workbook_close=99, workbook_open=100, workbook_rsi=15)) == "HOLD"
    assert rsi.on_snapshot(snapshot(workbook_close=101, workbook_open=100, workbook_rsi=21)) == "BUY"


def test_phase2_2b_confluence_and_stable_reduction_are_deterministic() -> None:
    adx_plugin = get_entry("xlsx_s1_0441")
    adx = adx_plugin.strategy_cls(adx_plugin.config_cls())
    assert adx.on_snapshot(snapshot(
        workbook_close=101, workbook_middle=100, workbook_adx=26,
        workbook_plus_di=30, workbook_minus_di=10,
    )) == "BUY"

    layered_plugin = get_entry("xlsx_s2_0338")
    layered = layered_plugin.strategy_cls(layered_plugin.config_cls())
    ordered = dict(workbook_close=105, workbook_fast=104, workbook_middle_ma=103,
                   workbook_slow=102, workbook_filter=101)
    assert layered.on_snapshot(snapshot(**ordered)) == "HOLD"
    assert layered.on_snapshot(snapshot(**ordered)) == "HOLD"
    assert layered.on_snapshot(snapshot(**ordered)) == "BUY"
    broken = dict(workbook_close=100, workbook_fast=99, workbook_middle_ma=103,
                  workbook_slow=102, workbook_filter=101)
    reduction = layered.on_snapshot(snapshot(**broken))
    assert reduction == "SELL"
    assert reduction.actions[0].quantity == 0.5
    assert layered.decision_position == 0.5


def test_phase2_2b_psar_and_ma_rsi_state_contracts_have_golden_entries() -> None:
    psar_plugin = get_entry("xlsx_s1_0440")
    psar = psar_plugin.strategy_cls(psar_plugin.config_cls())
    values = dict(workbook_close=104, workbook_psar=100,
                  workbook_psar_direction=1, workbook_atr=2)
    assert psar.on_snapshot(snapshot(**values)) == "HOLD"
    assert psar.on_snapshot(snapshot(**{**values, "workbook_atr": 0})) == "HOLD"
    assert psar.on_snapshot(snapshot(**values)) == "BUY"

    filter_plugin = get_entry("xlsx_s2_0126")
    filtered = filter_plugin.strategy_cls(filter_plugin.config_cls())
    assert filtered.on_snapshot(snapshot(workbook_close=101, workbook_middle=100, workbook_rsi=40)) == "HOLD"
    assert filtered.on_snapshot(snapshot(workbook_close=101, workbook_middle=100, workbook_rsi=25)) == "HOLD"
    assert filtered.on_snapshot(snapshot(workbook_close=101, workbook_middle=100, workbook_rsi=25)) == "HOLD"
    assert filtered.on_snapshot(snapshot(workbook_close=101, workbook_middle=100, workbook_rsi=31)) == "BUY"


def test_phase2_2b_existing_adx_family_reuse_preserves_full_entry_contract() -> None:
    plugin = get_entry("xlsx_s1_0453")
    strategy = plugin.strategy_cls(plugin.config_cls())
    assert strategy.on_snapshot(snapshot(
        workbook_close=100, workbook_fast=99, workbook_slow=100,
        workbook_adx=27, workbook_atr=2,
    )) == "HOLD"
    assert strategy.on_snapshot(snapshot(
        workbook_close=102, workbook_fast=101, workbook_slow=100,
        workbook_adx=27, workbook_atr=2,
    )) == "BUY"


def test_triple_sma_golden_ordering_entry_and_break_exit() -> None:
    plugin = get_entry("xlsx_s1_0025")
    strategy = plugin.strategy_cls(plugin.config_cls())
    assert strategy.on_snapshot(snapshot(workbook_close=100, workbook_fast=9, workbook_middle_ma=10, workbook_slow=8)) == "HOLD"
    assert strategy.on_snapshot(snapshot(workbook_close=100, workbook_fast=11, workbook_middle_ma=10, workbook_slow=8)) == "BUY"
    exit_signal = strategy.on_snapshot(snapshot(workbook_close=100, workbook_fast=9, workbook_middle_ma=10, workbook_slow=9.5))
    assert exit_signal == "EXIT"
    assert exit_signal.actions[0].close_all is True


def test_hma_cci_and_hlc_families_have_deterministic_golden_boundaries() -> None:
    hma_plugin = get_entry("xlsx_s1_0006")
    hma = hma_plugin.strategy_cls(hma_plugin.config_cls())
    assert hma.on_snapshot(snapshot(workbook_close=10, workbook_hma=10)) == "HOLD"
    assert hma.on_snapshot(snapshot(workbook_close=9, workbook_hma=9)) == "HOLD"
    assert hma.on_snapshot(snapshot(workbook_close=11, workbook_hma=10)) == "BUY"

    cci_plugin = get_entry("xlsx_s1_0016")
    cci = cci_plugin.strategy_cls(cci_plugin.config_cls())
    assert cci.on_snapshot(snapshot(workbook_close=99, workbook_middle=100, workbook_cci=-120)) == "HOLD"
    assert cci.on_snapshot(snapshot(workbook_close=101, workbook_middle=100, workbook_cci=-90)) == "BUY"
    assert cci.on_snapshot(snapshot(workbook_close=101, workbook_middle=100, workbook_cci=110)) == "EXIT"

    hlc_plugin = get_entry("xlsx_s1_0038")
    hlc = hlc_plugin.strategy_cls(hlc_plugin.config_cls())
    assert hlc.on_snapshot(snapshot(workbook_close=101, workbook_hlc_mean=100)) == "HOLD"
    assert hlc.on_snapshot(snapshot(workbook_close=102, workbook_hlc_mean=100)) == "BUY"


def test_adx_donchian_families_have_explicit_entry_and_exit_boundaries() -> None:
    plugin = get_entry("xlsx_s2_0230")
    strategy = plugin.strategy_cls(plugin.config_cls())
    flat = dict(workbook_close=100, workbook_adx=23, workbook_entry_up=1,
                workbook_entry_down=0, workbook_exit_up=0, workbook_exit_down=0)
    assert strategy.on_snapshot(snapshot(**flat)) == "HOLD"
    flat["workbook_adx"] = 25
    assert strategy.on_snapshot(snapshot(**flat)) == "BUY"
    flat.update(workbook_entry_up=0, workbook_exit_down=1)
    assert strategy.on_snapshot(snapshot(**flat)) == "EXIT"


def test_phase2_2c_persistent_macd_and_ao_families_have_golden_entries() -> None:
    macd_plugin = get_entry("xlsx_s1_0503")
    macd = macd_plugin.strategy_cls(macd_plugin.config_cls())
    assert macd.on_snapshot(snapshot(workbook_close=100, workbook_macd_dif=0.1,
                                     workbook_macd_signal=0.2, workbook_macd_histogram=-0.1)) == "HOLD"
    assert macd.on_snapshot(snapshot(workbook_close=101, workbook_macd_dif=0.15,
                                     workbook_macd_signal=0.2, workbook_macd_histogram=-0.05)) == "HOLD"
    assert macd.on_snapshot(snapshot(workbook_close=102, workbook_macd_dif=0.3,
                                     workbook_macd_signal=0.2, workbook_macd_histogram=0.1)) == "BUY"

    ao_plugin = get_entry("xlsx_s2_0019")
    ao = ao_plugin.strategy_cls(ao_plugin.config_cls())
    assert ao.on_snapshot(snapshot(workbook_close=100, workbook_ao=-1)) == "HOLD"
    assert ao.on_snapshot(snapshot(workbook_close=101, workbook_ao=0.2)) == "HOLD"
    assert ao.on_snapshot(snapshot(workbook_close=102, workbook_ao=0.5)) == "BUY"


def test_phase2_2c_grid_waits_for_fill_before_next_fractional_add() -> None:
    plugin = get_entry("xlsx_s2_0315")
    strategy = plugin.strategy_cls(plugin.config_cls())
    values = dict(workbook_close=100, workbook_atr=1, workbook_trend_up=1,
                  workbook_trend_down=0, workbook_entry_up=1, workbook_entry_down=0,
                  workbook_exit_up=0, workbook_exit_down=0)
    first = strategy.on_snapshot(snapshot(**values))
    assert first == "BUY" and first.actions[0].quantity == 0.25
    assert strategy.on_snapshot(snapshot(**{**values, "workbook_close": 102,
                                             "workbook_entry_up": 0})) == "HOLD"
    strategy.on_execution_report(ExecutionReport(
        backend="test", total_intents=1, total_fills=1,
        fills=[FillRecord("BTCUSDT-PERP.BINANCE", "BUY", 0.25, 100, 1)],
        positions=[], realized_pnl=0, unrealized_pnl=0,
    ))
    add = strategy.on_snapshot(snapshot(**{**values, "workbook_close": 101,
                                            "workbook_entry_up": 0}))
    assert add == "BUY" and add.actions[0].quantity == 0.25
    assert strategy.decision_position == 0.5


def test_phase2_2c_recent_extreme_and_stable_fractal_entries_are_explicit() -> None:
    recent_plugin = get_entry("xlsx_s1_0432")
    recent = recent_plugin.strategy_cls(recent_plugin.config_cls())
    base = dict(workbook_close=100, workbook_adx=26, workbook_entry_up=0,
                workbook_entry_down=0, workbook_exit_up=0, workbook_exit_down=0,
                workbook_plus_di=10, workbook_minus_di=20)
    assert recent.on_snapshot(snapshot(**base)) == "HOLD"
    assert recent.on_snapshot(snapshot(**{**base, "workbook_entry_up": 1,
                                           "workbook_plus_di": 30})) == "BUY"

    fractal_plugin = get_entry("xlsx_s2_0168")
    fractal = fractal_plugin.strategy_cls(fractal_plugin.config_cls())
    f = dict(workbook_close=101, workbook_middle=100, workbook_adx=25,
             workbook_upper_pulse=0, workbook_lower_pulse=1)
    assert fractal.on_snapshot(snapshot(**f)) == "HOLD"
    assert fractal.on_snapshot(snapshot(**f)) == "BUY"

    di_plugin = get_entry("xlsx_s2_0432")
    di = di_plugin.strategy_cls(di_plugin.config_cls())
    values = dict(workbook_close=100, workbook_adx=26, workbook_entry_up=1,
                  workbook_entry_down=0, workbook_exit_up=0, workbook_exit_down=0,
                  workbook_plus_di=30, workbook_minus_di=10)
    assert di.on_snapshot(snapshot(**values)) == "BUY"
    values.update(workbook_entry_up=0, workbook_plus_di=10, workbook_minus_di=30)
    assert di.on_snapshot(snapshot(**values)) == "EXIT"


def test_adx_di_cross_requires_a_completed_cross_not_only_a_level() -> None:
    plugin = get_entry("xlsx_s1_0024")
    strategy = plugin.strategy_cls(plugin.config_cls())
    common = dict(workbook_close=100, workbook_adx=26, workbook_entry_up=1,
                  workbook_entry_down=0, workbook_exit_up=0, workbook_exit_down=0)
    assert strategy.on_snapshot(snapshot(**common, workbook_plus_di=30, workbook_minus_di=10)) == "HOLD"
    assert strategy.on_snapshot(snapshot(**common, workbook_plus_di=31, workbook_minus_di=9)) == "HOLD"
    strategy = plugin.strategy_cls(plugin.config_cls())
    assert strategy.on_snapshot(snapshot(**common, workbook_plus_di=10, workbook_minus_di=30)) == "HOLD"
    assert strategy.on_snapshot(snapshot(**common, workbook_plus_di=31, workbook_minus_di=9)) == "BUY"


def test_fractal_adx_uses_confirmed_pulse_and_sma_filter() -> None:
    plugin = get_entry("xlsx_s2_0042")
    strategy = plugin.strategy_cls(plugin.config_cls())
    values = dict(workbook_close=101, workbook_middle=100, workbook_adx=25,
                  workbook_upper_pulse=0, workbook_lower_pulse=1)
    assert strategy.on_snapshot(snapshot(**values)) == "BUY"
    values.update(workbook_upper_pulse=1, workbook_lower_pulse=0)
    assert strategy.on_snapshot(snapshot(**values)) == "EXIT"


def test_sma_donchian_trend_uses_completed_slope_and_channel() -> None:
    plugin = get_entry("xlsx_s2_0708")
    strategy = plugin.strategy_cls(plugin.config_cls())
    values = dict(workbook_close=100, workbook_middle=99, workbook_entry_up=0,
                  workbook_entry_down=0, workbook_exit_up=0, workbook_exit_down=0)
    assert strategy.on_snapshot(snapshot(**values)) == "HOLD"
    values.update(workbook_middle=100, workbook_entry_up=1)
    assert strategy.on_snapshot(snapshot(**values)) == "BUY"
    values.update(workbook_entry_up=0, workbook_exit_down=1)
    assert strategy.on_snapshot(snapshot(**values)) == "EXIT"


def test_supertrend_family_routes_fill_anchored_state_through_adapter() -> None:
    plugin = get_entry("xlsx_s1_0029")
    strategy = plugin.strategy_cls(plugin.config_cls())
    values = dict(workbook_close=100, workbook_supertrend_direction=-1, workbook_atr=2)
    assert strategy.on_snapshot(snapshot(**values)) == "HOLD"
    values["workbook_supertrend_direction"] = 1
    signal = strategy.on_snapshot(snapshot(**values))
    assert signal == "BUY" and signal.actions[0].quantity == 1.0
    assert strategy.position == 0  # no fill report has arrived
