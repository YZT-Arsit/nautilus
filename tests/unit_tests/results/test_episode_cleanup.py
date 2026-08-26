from __future__ import annotations

import json

import pytest

from scripts.internal.cleanup_episode_diagnostics import canonical_candidates


def _validated_root(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "episode_diagnostics_validation.json").write_text(
        json.dumps({"status": "passed", "plot_validation_failure_count": 0}),
        encoding="utf-8",
    )


def test_cleanup_allowlist_preserves_canonical_episode_source_and_performance(tmp_path) -> None:
    old = tmp_path / "canonical"
    revised = tmp_path / "revised"
    case = old / "strategy/BTCUSDT/1m/lag0m/original"
    replacement_case = revised / "strategy/BTCUSDT/1m/lag0m/original"
    case.mkdir(parents=True)
    replacement_case.mkdir(parents=True)
    _validated_root(revised)
    generated = case / "episode_metrics.parquet"
    replacement = replacement_case / generated.name
    generated.write_bytes(b"old")
    replacement.write_bytes(b"new")
    (case / "per_trade_break_even.csv").write_text("canonical\n", encoding="utf-8")
    (case / "BTCUSDT_1m_lag0m_original_performance.png").write_bytes(b"canonical")

    rows = canonical_candidates(old, revised)

    assert [row["path"] for row in rows] == [str(generated.resolve())]


def test_cleanup_refuses_candidate_without_validated_replacement(tmp_path) -> None:
    old = tmp_path / "canonical"
    revised = tmp_path / "revised"
    old.mkdir()
    _validated_root(revised)
    (old / "episode_metric_summary.csv").write_text("old\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="no validated replacement"):
        canonical_candidates(old, revised)
