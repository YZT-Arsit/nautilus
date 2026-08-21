from strategy_framework.modules import SessionFlattenModule, SessionRiskState


MINUTE = 60_000_000_000
DAY = 86_400_000_000_000


def test_session_flatten_module_uses_last_executable_opportunity() -> None:
    module = SessionFlattenModule()
    assert module.should_flatten(decision_time_ns=DAY - MINUTE, execution_lag_ns=0)
    assert module.should_flatten(decision_time_ns=DAY - 2 * MINUTE, execution_lag_ns=MINUTE)
    assert not module.should_flatten(decision_time_ns=DAY, execution_lag_ns=0)


def test_session_risk_resets_at_utc_boundary_and_counts_real_entries() -> None:
    state = SessionRiskState()
    state.update(event_time_ns=DAY - 1, realized_pnl_delta=-10, executed_entry=True)
    assert state.loss_limit_hit(9)
    assert state.entry_limit_hit(1)
    state.update(event_time_ns=DAY, unrealized_pnl=-2)
    assert state.total_pnl == -2
    assert state.entry_count == 0
