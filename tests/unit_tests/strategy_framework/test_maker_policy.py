from __future__ import annotations

import pytest

from strategy_framework.execution.maker_policy import MakerDataTier
from strategy_framework.execution.maker_policy import NextDecisionCancelState
from strategy_framework.execution.maker_policy import PureMakerLifecycleState
from strategy_framework.execution.maker_policy import passive_trade_only_price
from strategy_framework.execution.maker_policy import queue_mode_allowed


def test_delta_uses_actual_filled_position_and_cancels_old_remainder() -> None:
    state = NextDecisionCancelState()
    assert state.next_decision(1.0) == 1.0
    state.apply_fill(0.35)
    assert state.actual_position == pytest.approx(0.35)
    assert state.next_decision(-1.0) == pytest.approx(-1.35)
    assert state.canceled_quantity == pytest.approx(0.65)


def test_partial_fill_cannot_exceed_remainder_or_reverse_side() -> None:
    state = NextDecisionCancelState()
    state.next_decision(1.0)
    with pytest.raises(ValueError):
        state.apply_fill(-0.1)
    with pytest.raises(ValueError):
        state.apply_fill(1.1)


def test_queue_mode_is_gated_by_market_data_tier() -> None:
    assert queue_mode_allowed(MakerDataTier.L2_MBP)
    assert queue_mode_allowed(MakerDataTier.L3_MBO)
    assert not queue_mode_allowed(MakerDataTier.L1)
    assert not queue_mode_allowed(MakerDataTier.TRADE_ONLY)


def test_trade_only_price_is_explicitly_passive_relative_to_last_trade() -> None:
    assert passive_trade_only_price(100.0, 0.1, 1.0) == pytest.approx(99.9)
    assert passive_trade_only_price(100.0, 0.1, -1.0) == pytest.approx(100.1)


def test_native_quantity_alignment_preserves_continuous_target_error() -> None:
    state = NextDecisionCancelState()
    assert state.next_decision(0.06) == pytest.approx(0.06)
    state.align_resting_quantity(0.1)
    state.apply_fill(0.1)
    assert state.actual_position == pytest.approx(0.1)
    assert state.desired_target == pytest.approx(0.06)
    assert state.target_error == pytest.approx(0.04)


def test_gtc_state_keeps_remainder_until_explicit_invalidation() -> None:
    state = PureMakerLifecycleState()
    assert state.set_target(1.0) == pytest.approx(1.0)
    state.align_resting_quantity(1.0)
    state.apply_fill(0.35)
    assert state.resting_remaining == pytest.approx(0.65)
    assert state.set_target(1.0) == pytest.approx(0.65)
    assert state.canceled_quantity == 0.0
    assert state.cancel_remainder() == pytest.approx(0.65)
    assert state.set_target(-1.0) == pytest.approx(-1.35)


def test_requote_state_cancels_only_unfilled_remainder() -> None:
    state = PureMakerLifecycleState()
    state.set_target(1.0)
    state.align_resting_quantity(1.0)
    state.apply_fill(0.4)
    assert state.cancel_remainder() == pytest.approx(0.6)
    assert state.required_delta == pytest.approx(0.6)
    state.align_resting_quantity(0.6)
    state.apply_fill(0.6)
    assert state.actual_position == pytest.approx(1.0)
    assert state.target_error == pytest.approx(0.0)
