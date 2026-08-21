import math

import pytest

from strategy_framework.module_registry import MODULE_REGISTRY, get_module, register_module
from strategy_framework.modules import (
    AdxExposureModule, AtrHardStopModule, AtrLadderExitModule,
    DonchianExitModule, GridPyramidState, LevelEvent, LevelEventState,
    ModuleContext, PyramidDirection,
)


def module(module_id: str = "test") -> AtrLadderExitModule:
    return AtrLadderExitModule(module_id, (1.0, 2.0), (0.2, 0.3), 3.0, 0.6)


def test_atr_ladder_returns_fill_execution_target_without_owning_position() -> None:
    item = module()
    base = dict(side=1, entry_price=100.0, atr=10.0)
    assert item.evaluate(ModuleContext(current_price=105.0, **base)).target_exposure == 1.0
    assert math.isclose(item.evaluate(ModuleContext(current_price=111.0, **base)).target_exposure, 0.8)
    assert math.isclose(item.evaluate(ModuleContext(current_price=121.0, **base)).target_exposure, 0.5)
    assert item.evaluate(ModuleContext(current_price=130.0, **base)).target_exposure == 0.0
    assert item.evaluate(ModuleContext(current_price=94.0, **base)).target_exposure == 0.0


def test_module_registry_rejects_identity_collisions() -> None:
    MODULE_REGISTRY.clear(); register_module(module("unique"))
    assert get_module("unique").module_id == "unique"
    with pytest.raises(ValueError):
        register_module(module("unique"))


def test_hard_stop_and_channel_exit_use_only_explicit_execution_inputs() -> None:
    context = ModuleContext(side=1, entry_price=100, current_price=97, atr=2,
                            upper_channel=110, lower_channel=98)
    assert AtrHardStopModule("stop", 2).evaluate(context).target_exposure == 1.0
    assert DonchianExitModule("channel", 10).evaluate(context).target_exposure == 0.0
    stopped = ModuleContext(side=1, entry_price=100, current_price=96, atr=2)
    assert AtrHardStopModule("stop", 2).evaluate(stopped).target_exposure == 0.0


def test_adx_exposure_preserves_explicit_three_regimes() -> None:
    item = AdxExposureModule("adx", 25, 20, 0.5, 0.3)
    base = dict(side=1, entry_price=100, current_price=100, atr=2)
    assert item.evaluate(ModuleContext(adx=26, **base)).target_exposure == 1.0
    assert item.evaluate(ModuleContext(adx=22, **base)).target_exposure == 0.5
    assert item.evaluate(ModuleContext(adx=19, **base)).target_exposure == 0.3


def test_favorable_pyramid_is_fill_synchronized_capped_and_not_duplicated() -> None:
    state = GridPyramidState()
    assert state.initial_target(1) == 0.25
    assert state.add_target(price=102, atr=1, direction=PyramidDirection.FAVORABLE) is None
    state.synchronize_fill(position=0.25, fill_price=100)
    for expected, price in ((0.5, 101), (0.75, 102), (1.0, 103)):
        assert state.add_target(price=price, atr=1, direction=PyramidDirection.FAVORABLE) == expected
        assert state.add_target(price=price, atr=1, direction=PyramidDirection.FAVORABLE) is None
        state.synchronize_fill(position=expected, fill_price=price)
    assert state.add_target(price=104, atr=1, direction=PyramidDirection.FAVORABLE) is None


def test_adverse_grid_and_partial_reduction_preserve_fractional_exposure() -> None:
    state = GridPyramidState()
    state.initial_target(1)
    state.synchronize_fill(position=0.25, fill_price=100)
    assert state.add_target(price=99, atr=1, direction=PyramidDirection.ADVERSE) == 0.5
    state.synchronize_fill(position=0.5, fill_price=99)
    assert state.reduction_target(level_id="tp1") == 0.25
    assert state.reduction_target(level_id="tp1") is None
    state.synchronize_fill(position=0.25, fill_price=101)
    assert state.current_exposure == 0.25


def test_level_event_requires_breakout_before_retest_and_distinguishes_failure() -> None:
    state = LevelEventState()
    assert state.update(previous_close=99, open_=99, high=100.2, low=98.5, close=99.5,
                        level=100, tolerance=0.25) is LevelEvent.UNTOUCHED
    assert state.update(previous_close=99.5, open_=100, high=102, low=99.8, close=101,
                        level=100, tolerance=0.25) is LevelEvent.BROKEN_ABOVE
    assert state.update(previous_close=101, open_=100.1, high=100.8, low=99.9, close=100.5,
                        level=100, tolerance=0.25) is LevelEvent.RECLAIMED
    failed = LevelEventState(LevelEvent.BROKEN_ABOVE)
    assert failed.update(previous_close=101, open_=100, high=100.2, low=98.5, close=99,
                         level=100, tolerance=0.25) is LevelEvent.REJECTED
