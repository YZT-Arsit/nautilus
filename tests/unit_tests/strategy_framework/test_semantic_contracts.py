from strategy_framework.semantic_contracts import (
    CONTRACTS,
    PullbackState,
    SemanticProvenance,
    all_required,
    contract,
    grid_target,
    level_tolerance,
    near_level,
    persistent,
    prior_new_high,
    prior_new_low,
    reduce_current,
    rejection,
    stable_above,
    stable_below,
    stabilized,
    turn_down,
    turn_up,
)


def test_registry_ids_are_unique_and_versioned() -> None:
    ids = [item.versioned_id for item in CONTRACTS]
    assert len(ids) == len(set(ids))
    assert contract("ATR14_DEFAULT_V1").defaults() == {"period": 14}
    assert contract("ATR14_DEFAULT_V1").provenance is SemanticProvenance.PARAMETER_DEFAULTED


def test_turn_contract_uses_slope_sign_change() -> None:
    assert turn_up(3.0, 2.0, 2.5)
    assert not turn_up(1.0, 2.0, 3.0)
    assert turn_down(2.0, 3.0, 2.5)


def test_persistence_and_stable_close_require_two_completed_observations() -> None:
    assert not persistent([True])
    assert persistent([False, True, True])
    assert not stable_above([99.0, 101.0], [100.0, 100.0])
    assert stable_above([101.0, 102.0], [100.0, 100.0])
    assert stable_below([99.0, 98.0], [100.0, 100.0])


def test_prior_extreme_excludes_current_observation() -> None:
    assert prior_new_high(11.0, [8.0, 10.0, 9.0])
    assert not prior_new_high(10.0, [8.0, 10.0, 9.0])
    assert prior_new_low(7.0, [8.0, 10.0, 9.0])
    assert not prior_new_low(8.0, [8.0, 10.0, 9.0])


def test_pullback_requires_prior_breakout_and_never_accepts_first_touch() -> None:
    state = PullbackState(direction=1)
    tolerance = level_tolerance(atr=4.0)
    assert not state.update(close=100.5, low=99.5, high=101.0, level=100.0, tolerance=tolerance)
    assert not state.update(close=103.0, low=102.0, high=104.0, level=100.0, tolerance=tolerance)
    assert state.update(close=100.5, low=99.8, high=101.0, level=100.0, tolerance=tolerance)


def test_rejection_grid_reduction_and_confluence_contracts() -> None:
    assert rejection(direction=-1, open_=101.0, close=99.5, low=99.0, high=101.0,
                     level=100.0, tolerance=0.25)
    assert grid_target(4, direction=1) == 1.0
    assert grid_target(4, direction=-1) == -1.0
    assert reduce_current(1.0) == 0.5
    assert all_required([True, True])
    assert not all_required([True, False])
    assert near_level(100.9, 100.0, level_tolerance(4.0))
    assert stabilized(-1.0, 0.0, after_decline=True)


def test_all_policy_defaults_are_centralized_and_auditable() -> None:
    expected = {
        "PERSISTENCE_2BAR_V1": {"bars": 2},
        "RECENT_EXTREME_PRIOR_20_V1": {"lookback": 20},
        "LEVEL_TOLERANCE_ATR025_V1": {"atr_period": 14, "atr_multiple": 0.25},
        "CONFIRMED_FRACTAL_2X2_V1": {"side_bars": 2},
        "DIVERGENCE_LOOKBACK_60_V1": {"lookback": 60},
        "REDUCE_HALF_CURRENT_V1": {"fraction": 0.5},
        "ADD_QUARTER_EXPOSURE_V1": {"add_fraction": 0.25, "max_abs_exposure": 1.0},
    }
    assert {name: contract(name).defaults() for name in expected} == expected
