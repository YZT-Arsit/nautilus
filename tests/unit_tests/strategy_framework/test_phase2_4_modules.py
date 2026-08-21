import math

from strategy_framework.module_composition import ModulePriority
from strategy_framework.module_composition import ModuleProposal
from strategy_framework.module_composition import resolve_module_proposals
from strategy_framework.modules import AccountDrawdownControlModule
from strategy_framework.modules import AtrAdverseReductionModule
from strategy_framework.modules import AtrBreakevenTrailingModule
from strategy_framework.modules import CompositeRiskModule
from strategy_framework.modules import DailyRiskControlModule
from strategy_framework.modules import EntryExposureCapModule
from strategy_framework.modules import ExposureCapModule
from strategy_framework.modules import FeatureExitCondition
from strategy_framework.modules import FeatureExitModule
from strategy_framework.modules import FeatureExposureModule
from strategy_framework.modules import FixedPercentageStopModule
from strategy_framework.modules import ModuleContext
from strategy_framework.modules import TimeExitModule
from strategy_framework.modules import VolatilityExposureModule


def context(**updates):
    values = dict(side=1, entry_price=100.0, current_price=100.0, atr=2.0)
    values.update(updates)
    return ModuleContext(**values)


def test_percentage_stop_mirrors_long_and_short_at_exact_boundary() -> None:
    module = FixedPercentageStopModule("pct", 0.08)
    assert module.evaluate(context(current_price=92.0)).target_exposure == 0.0
    assert module.evaluate(context(side=-1, current_price=108.0)).target_exposure == 0.0
    assert module.evaluate(context(current_price=92.01)).target_exposure == 1.0


def test_breakeven_trailing_is_fill_synchronized_and_ratchets_only_favorably() -> None:
    module = AtrBreakevenTrailingModule("trail", 1.0, 0.5, 0.7, 1.0)
    module.synchronize_fill(position=0.5, fill_price=100.0)
    first = context(current_price=104.0, highest_price_since_entry=105.0, current_exposure=0.5)
    assert module.evaluate(first).target_exposure == 1.0
    assert module.stop_price == 103.0
    module.evaluate(
        context(current_price=103.5, highest_price_since_entry=104.0, current_exposure=0.5)
    )
    assert module.stop_price == 103.0
    assert (
        module.evaluate(
            context(current_price=103.0, highest_price_since_entry=105.0)
        ).target_exposure
        == 0.0
    )
    module.synchronize_fill(position=0.0, fill_price=103.0)
    assert module.entry_price is None and module.stop_price is None


def test_time_exit_and_exposure_caps_preserve_fractional_exposure() -> None:
    assert TimeExitModule("time", 5).evaluate(context(bars_held=4)).target_exposure == 1.0
    assert TimeExitModule("time", 5).evaluate(context(bars_held=5)).target_exposure == 0.0
    assert ExposureCapModule("cap", 0.25).evaluate(context()).target_exposure == 0.25
    entry_cap = EntryExposureCapModule("entry", 0.02)
    assert entry_cap.evaluate(context(current_exposure=0.0)).target_exposure == 0.02
    assert entry_cap.evaluate(context(current_exposure=0.015)).target_exposure == 0.015


def test_volatility_tiers_and_adverse_reductions_use_explicit_targets() -> None:
    volatility = VolatilityExposureModule("vol", (30.0, 60.0, 90.0), (1.0, 0.6, 0.3, 0.0), 90.0)
    assert volatility.evaluate(context(volatility_percentile=20)).target_exposure == 1.0
    assert volatility.evaluate(context(volatility_percentile=45)).target_exposure == 0.6
    assert (
        volatility.evaluate(
            context(volatility_percentile=95, current_exposure=0.25)
        ).target_exposure
        == 0.25
    )
    adverse = AtrAdverseReductionModule("loss", (1.0, 2.0, 3.0), (0.7, 0.4, 0.0))
    assert math.isclose(adverse.evaluate(context(current_price=98.0)).target_exposure, 0.7)
    assert adverse.evaluate(context(current_price=94.0)).target_exposure == 0.0


def test_feature_modules_use_typed_host_values_not_eval() -> None:
    exit_module = FeatureExitModule("exit", (FeatureExitCondition("roc_below", side="long"),))
    assert exit_module.evaluate(context(feature_values={"roc_below": True})).target_exposure == 0.0
    assert (
        exit_module.evaluate(context(side=-1, feature_values={"roc_below": True})).target_exposure
        == 1.0
    )
    reduction = FeatureExposureModule(
        "reduce",
        FeatureExitCondition("adx", operator="lt", threshold=20.0),
        0.5,
    )
    assert reduction.evaluate(context(feature_values={"adx": 19.0})).target_exposure == 0.5


def test_account_and_daily_risk_use_current_state_only() -> None:
    drawdown = AccountDrawdownControlModule("dd", 0.10, 0.12)
    assert drawdown.evaluate(context(account_drawdown=-0.10)).target_exposure == 0.5
    assert drawdown.evaluate(context(account_drawdown=-0.12)).target_exposure == 0.0
    daily = DailyRiskControlModule("daily", maximum_loss=0.03, maximum_entries=2)
    day = 86_400_000_000_000
    daily.update_execution(event_time_ns=day - 1, realized_pnl_delta=-0.02, executed_entry=True)
    assert daily.entry_allowed()
    daily.update_execution(event_time_ns=day - 1, realized_pnl_delta=-0.01, executed_entry=True)
    assert not daily.entry_allowed()
    daily.update_execution(event_time_ns=day, realized_pnl_delta=0.0)
    assert daily.entry_allowed()


def test_composite_and_priority_never_allow_add_to_override_risk() -> None:
    composite = CompositeRiskModule(
        "risk",
        (ExposureCapModule("cap", 0.5), FixedPercentageStopModule("stop", 0.05)),
    )
    assert composite.evaluate(context(current_price=94.0)).target_exposure == 0.0
    proposals = [
        ModuleProposal("pyramid", 1.0, ModulePriority.ADD_POSITION, "add"),
        ModuleProposal("take_profit", 0.5, ModulePriority.REDUCE_POSITION, "reduce"),
        ModuleProposal("hard_stop", 0.0, ModulePriority.EXIT, "stop"),
    ]
    result = resolve_module_proposals(base_target=0.75, current_exposure=0.75, proposals=proposals)
    assert result.module_id == "hard_stop" and result.target_exposure == 0.0


def test_session_flatten_priority_beats_same_timestamp_reentry() -> None:
    result = resolve_module_proposals(
        base_target=-1.0,
        current_exposure=1.0,
        proposals=[
            ModuleProposal("reentry", -1.0, ModulePriority.ADD_POSITION, "reverse"),
            ModuleProposal("session", 0.0, ModulePriority.SESSION_FLATTEN, "session_flatten"),
        ],
    )
    assert result.module_id == "session" and result.target_exposure == 0.0


def test_take_profit_and_time_exit_beat_add_requests() -> None:
    for exit_id in ("take_profit", "time_exit"):
        result = resolve_module_proposals(
            base_target=1.0,
            current_exposure=0.75,
            proposals=[
                ModuleProposal("pyramid", 1.0, ModulePriority.ADD_POSITION, "add"),
                ModuleProposal(exit_id, 0.0, ModulePriority.EXIT, exit_id),
            ],
        )
        assert result.module_id == exit_id and result.target_exposure == 0.0


def test_exposure_cap_bounds_add_and_partial_reduction_beats_reversal_add() -> None:
    capped = resolve_module_proposals(
        base_target=1.0,
        current_exposure=0.5,
        proposals=[
            ModuleProposal("cap", 0.6, ModulePriority.EXPOSURE_CAP, "cap"),
            ModuleProposal("add", 1.0, ModulePriority.ADD_POSITION, "add"),
        ],
    )
    assert capped.target_exposure == 0.6
    reduced = resolve_module_proposals(
        base_target=-1.0,
        current_exposure=1.0,
        proposals=[
            ModuleProposal("reverse", -1.0, ModulePriority.ADD_POSITION, "reverse"),
            ModuleProposal("partial", 0.5, ModulePriority.REDUCE_POSITION, "reduce"),
        ],
    )
    assert reduced.module_id == "partial" and reduced.target_exposure == 0.5
