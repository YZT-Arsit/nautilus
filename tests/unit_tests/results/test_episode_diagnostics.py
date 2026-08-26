from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from results.episode_diagnostics import choose_histogram_spec
from results.episode_diagnostics import enrich_episode_frame
from results.episode_diagnostics import histogram_rows
from results.episode_diagnostics import METRIC_SPEC_BY_ID
from results.episode_diagnostics import maximum_formula_residual
from results.episode_diagnostics import metric_values
from results.episode_diagnostics import nice_step
from results.episode_diagnostics import normalize_episode_schema
from results.episode_diagnostics import validate_premium_pair
from results.trade_episode import build_de_risk_episodes


def _episodes(premium: str, returns: list[float]) -> list[dict]:
    rows, _ = build_de_risk_episodes(
        event_time_ns=[0, 60_000_000_000, 120_000_000_000, 180_000_000_000],
        executed_position=[1.0, 1.0, -0.5, 0.0],
        turnover_increment=[1.0, 0.0, 1.5, 0.5],
        gross_return_increment=returns,
        strategy="test",
        symbol="BTCUSDT",
        granularity="1m bar",
        lag="1m physical-time",
        premium_mode=premium,
    )
    return rows


def test_enrichment_aligns_duration_return_turnover_and_be() -> None:
    raw = pd.DataFrame(_episodes("included", [0.0, 0.01, -0.005, 0.002]))
    frame = enrich_episode_frame(raw)
    assert frame["holding_duration_seconds"].tolist() == [120.0, 60.0]
    assert frame["holding_duration_minutes"].tolist() == [2.0, 1.0]
    assert np.array_equal(frame["episode_return"], raw["delta_gross_return"])
    assert np.array_equal(frame["episode_turnover"], raw["delta_turnover"])
    assert np.array_equal(frame["episode_turnover_pct"], raw["delta_turnover"] * 100.0)
    assert maximum_formula_residual(frame) < 1e-15


def test_turnover_display_is_percent_but_break_even_uses_raw_turnover() -> None:
    raw = pd.DataFrame(_episodes("included", [0.0, 0.01, -0.005, 0.002]))
    frame = enrich_episode_frame(raw)
    displayed = metric_values(frame, "episode_turnover")
    assert np.array_equal(displayed, frame["delta_turnover"].to_numpy() * 100.0)
    assert METRIC_SPEC_BY_ID["episode_turnover"].display_unit == "% of capital"
    assert maximum_formula_residual(frame) < 1e-15


def test_explicit_historical_aliases_are_normalized() -> None:
    raw = pd.DataFrame(_episodes("included", [0.0, 0.01, -0.005, 0.002])).rename(
        columns={"start_timestamp": "start_time", "completion_timestamp": "completion_time"}
    )
    normalized, metadata = normalize_episode_schema(raw)
    assert "start_timestamp" in normalized
    assert "completion_timestamp" in normalized
    assert metadata["aliases_used"] == {
        "start_time": "start_timestamp",
        "completion_time": "completion_timestamp",
    }


def test_unknown_episode_schema_fails_explicitly() -> None:
    raw = pd.DataFrame(_episodes("included", [0.0, 0.01, -0.005, 0.002])).drop(
        columns="delta_turnover"
    )
    with pytest.raises(ValueError, match="unsupported episode schema"):
        normalize_episode_schema(raw)


def test_premium_pair_has_identical_episode_execution_identity() -> None:
    included = _episodes("included", [0.0, 0.01, -0.005, 0.002])
    excluded = _episodes("excluded", [0.0, 0.009, -0.006, 0.001])
    validation = validate_premium_pair(enrich_episode_frame(pd.DataFrame(included + excluded)))
    assert validation == {"episode_count": 2, "maximum_identity_residual": 0.0}


def test_premium_pair_rejects_turnover_mismatch() -> None:
    included = _episodes("included", [0.0, 0.01, -0.005, 0.002])
    excluded = _episodes("excluded", [0.0, 0.009, -0.006, 0.001])
    excluded[0]["delta_turnover"] += 0.01
    with pytest.raises(ValueError, match="delta_turnover"):
        validate_premium_pair(enrich_episode_frame(pd.DataFrame(included + excluded)))


def test_signed_bins_are_zero_anchored_and_deterministic() -> None:
    values = np.array([-4.2, -0.2, 0.1, 0.9, 1.5, 99.0])
    first = choose_histogram_spec(
        values,
        metric="break_even_bps",
        display_unit="bps",
        signed=True,
        minimum_width=1.0,
    )
    second = choose_histogram_spec(
        values,
        metric="break_even_bps",
        display_unit="bps",
        signed=True,
        minimum_width=1.0,
    )
    assert first == second
    assert 0.0 in first.edges
    assert first.bin_width in {1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0}


def test_histogram_and_frequency_counts_include_outliers() -> None:
    values = np.array([-100.0, -0.5, 0.2, 0.8, 1.2, 200.0])
    spec = choose_histogram_spec(
        values,
        metric="break_even_bps",
        display_unit="bps",
        signed=True,
        minimum_width=1.0,
    )
    rows = histogram_rows(values, spec)
    assert sum(row["count"] for row in rows) == len(values)
    central = [row for row in rows if row["bin_kind"] == "central"]
    assert all(row["bin_center"] == (row["bin_left"] + row["bin_right"]) / 2 for row in central)


def test_nice_step_uses_one_two_five_sequence() -> None:
    assert nice_step(0.25, target_bins=60) == 0.005
    assert nice_step(60.0, target_bins=60) == 1.0
    assert nice_step(120.0, target_bins=60) == 2.0
    assert nice_step(250.0, target_bins=60) == 5.0


def test_negative_holding_duration_is_rejected() -> None:
    row = _episodes("included", [0.0, 0.01, -0.005, 0.002])[0]
    row["completion_timestamp"], row["start_timestamp"] = (
        row["start_timestamp"],
        row["completion_timestamp"],
    )
    with pytest.raises(ValueError, match="precedes"):
        enrich_episode_frame(pd.DataFrame([row]))


def test_partial_reduction_keeps_one_shared_episode_boundary() -> None:
    rows, summary = build_de_risk_episodes(
        event_time_ns=[0, 60_000_000_000, 120_000_000_000],
        executed_position=[1.0, 0.5, 0.0],
        turnover_increment=[1.0, 0.5, 0.5],
        gross_return_increment=[0.0, 0.01, 0.02],
        strategy="partial",
        symbol="BTCUSDT",
        granularity="1m bar",
        lag="0m physical-time",
        premium_mode="included",
    )
    frame = enrich_episode_frame(pd.DataFrame(rows))
    assert frame["episode_id"].tolist() == [1, 2]
    assert frame["completion_reason"].tolist() == ["partial_reduce", "close"]
    assert frame["holding_duration_seconds"].tolist() == [60.0, 60.0]
    assert summary["partial_reduce_count"] == 1
    assert maximum_formula_residual(frame) < 1e-15
