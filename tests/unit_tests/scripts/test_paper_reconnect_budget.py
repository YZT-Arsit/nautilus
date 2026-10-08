from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/run_paper_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("paper_orchestrator_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_reconnect_budget_enters_degraded_wait_and_recovers() -> None:
    budget = MODULE.ReconnectBudget(
        max_attempts=2, window_seconds=60.0, degraded_wait_seconds=30.0,
    )
    assert budget.delay_before_attempt(100.0) == 0.0
    assert budget.delay_before_attempt(101.0) == 0.0
    assert budget.delay_before_attempt(102.0) == 58.0
    assert budget.suppressed == 1
    assert budget.degraded_waits == 1

    budget.record_after_wait(160.0)
    assert budget.delay_before_attempt(161.0) == 0.0


def test_reconnect_budget_validates_configuration() -> None:
    try:
        MODULE.ReconnectBudget(max_attempts=0, window_seconds=60.0, degraded_wait_seconds=30.0)
    except ValueError as exc:
        assert "invalid reconnect budget" in str(exc)
    else:
        raise AssertionError("invalid budget was accepted")


def test_stale_detection_only_owns_healthy_state() -> None:
    assert MODULE.should_trigger_stale(
        state="HEALTHY", last_receive=10.0, now=21.0, threshold=10.0,
        reconnect_requested=False,
    )
    for state in ("STALE_DETECTED", "RECOVERING", "VALIDATING"):
        assert not MODULE.should_trigger_stale(
            state=state, last_receive=10.0, now=100.0, threshold=10.0,
            reconnect_requested=False,
        )


def test_validation_timeout_is_single_bounded_request() -> None:
    assert MODULE.should_timeout_validation(
        state="VALIDATING", validation_started=10.0, now=31.0,
        validation_timeout=20.0, reconnect_requested=False,
    )
    assert not MODULE.should_timeout_validation(
        state="VALIDATING", validation_started=10.0, now=31.0,
        validation_timeout=20.0, reconnect_requested=True,
    )
