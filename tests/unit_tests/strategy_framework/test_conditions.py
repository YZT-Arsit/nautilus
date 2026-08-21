from strategy_framework.conditions import (
    Comparison, ConsecutiveState, PreviousValues, compare, cross_above, cross_below,
)


def test_crosses_use_current_and_previous_completed_values() -> None:
    assert cross_above(1.0, 1.0, 2.0, 1.0)
    assert not cross_above(2.0, 1.0, 3.0, 1.0)
    assert cross_below(1.0, 1.0, 0.0, 1.0)
    assert not cross_below(0.0, 1.0, -1.0, 1.0)


def test_previous_and_consecutive_state_have_no_current_value_lookahead() -> None:
    history = PreviousValues(3)
    history.push(10.0); history.push(20.0)
    assert history.previous() == 20.0
    assert history.previous(2) == 10.0
    state = ConsecutiveState(2)
    assert not state.update(True)
    assert state.update(True)
    assert not state.update(False)
    assert compare(2.0, Comparison.GE, 2.0)

