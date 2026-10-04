from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts/internal/run_dynamic_breakout_missing_reverse.py"
)
SPEC = importlib.util.spec_from_file_location("missing_reverse_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_refuses_to_start_while_p0_is_running(tmp_path: Path) -> None:
    status = (
        tmp_path / "outputs/baseline_evaluation/paper_market_data_continuity_repair/"
        "gate_status.json"
    )
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": "RUNNING_30M_NETWORK_SMOKE"}))
    with pytest.raises(RuntimeError, match="P0_ACTIVE_REFUSING_P1_START"):
        MODULE.assert_p0_idle(tmp_path)


def test_accepts_completed_p0(tmp_path: Path) -> None:
    status = (
        tmp_path / "outputs/baseline_evaluation/paper_market_data_continuity_repair/"
        "gate_status.json"
    )
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": "PASSED"}))
    assert MODULE.assert_p0_idle(tmp_path) == "PASSED"


def write_active_p0(tmp_path: Path, heartbeat: dict) -> None:
    experiment = tmp_path / "paper_smoke"
    heartbeat_path = experiment / "workers/BTCUSDT/health/heartbeat.json"
    heartbeat_path.parent.mkdir(parents=True)
    heartbeat_path.write_text(json.dumps(heartbeat))
    status = (
        tmp_path / "outputs/baseline_evaluation/paper_market_data_continuity_repair/"
        "gate_status.json"
    )
    status.parent.mkdir(parents=True)
    status.write_text(
        json.dumps(
            {
                "status": "RUNNING_30M_NETWORK_SMOKE",
                "smoke_experiment": str(experiment),
            }
        )
    )


def test_bounded_mode_accepts_healthy_active_p0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_active_p0(
        tmp_path,
        {
            "alive": True,
            "feed_state": {"BTCUSDT": "HEALTHY"},
            "workers_running": {"BTCUSDT": True},
            "worker_errors": 0,
            "reconnects": 0,
            "stale_incidents": 0,
            "dropped_events": 0,
            "queue_backlog": 2,
        },
    )
    monkeypatch.setattr(MODULE, "free_memory_gib", lambda: 26.9)
    monkeypatch.setattr(MODULE, "set_idle_process_priority", lambda: None)
    audit = MODULE.start_safety(tmp_path, bounded_during_p0=True)
    assert audit["safe"] is True
    assert audit["free_memory_gib"] == 26.9


def test_bounded_mode_flags_unhealthy_active_p0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_active_p0(
        tmp_path,
        {
            "alive": True,
            "feed_state": {"BTCUSDT": "STALE"},
            "workers_running": {"BTCUSDT": True},
            "worker_errors": 0,
            "reconnects": 4,
            "stale_incidents": 1,
            "dropped_events": 2,
            "queue_backlog": 999,
        },
    )
    monkeypatch.setattr(MODULE, "free_memory_gib", lambda: 8.0)
    monkeypatch.setattr(MODULE, "set_idle_process_priority", lambda: None)
    audit = MODULE.start_safety(tmp_path, bounded_during_p0=True)
    assert audit["safe"] is False
    assert "FREE_MEMORY_BELOW_12_GIB" in audit["reasons"]
    assert "P0_FEED_NOT_HEALTHY" in audit["reasons"]
    assert "P0_QUEUE_BACKLOG_ABOVE_LIMIT" in audit["reasons"]
