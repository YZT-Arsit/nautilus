from __future__ import annotations

import csv
import json
from pathlib import Path

from strategy_framework.parameter_search import PROTOCOL_VERSION
from strategy_framework.parameter_search import generate_candidates
from strategy_framework.parameter_search import is_wave1_spec
from strategy_framework.parameter_search import logical_checkpoint_id
from strategy_framework.parameter_search import physical_cache_key
from strategy_framework.parameter_search import protocol_amendment
from strategy_framework.parameter_search import select_candidate
from strategy_framework.parameter_search import validate_protocol
from strategy_framework.parameter_search import validation_eligibility


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"


def _rows() -> list[dict[str, str]]:
    with (AUDIT / "parameter_search_manifest.csv").open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_protocol_amendment_resolves_every_pre_run_blocker() -> None:
    protocol = protocol_amendment()
    assert validate_protocol(protocol) == []
    assert protocol["eligibility"]["min_validation_trades"] == 5
    assert protocol["eligibility"]["max_drawdown_constraint"]["enabled"] is False
    assert protocol["eligibility"]["minimum_break_even_bps_constraint"]["enabled"] is False
    assert protocol["selection"]["no_eligible_candidate_policy"] == "BASELINE_FALLBACK"
    assert protocol["evaluation"]["logical_evaluation_count"] == 1792
    assert protocol["future_wave3"]["corrected_logical_estimate"] == 10318


def test_wave1_scope_candidates_and_baselines_are_unchanged() -> None:
    specs = [row for row in _rows() if is_wave1_spec(row)]
    candidates = [generate_candidates(row) for row in specs]
    assert len(specs) == 23
    assert sum(map(len, candidates)) == 105
    assert (
        sum(sum(item.candidate_role == "BASELINE" for item in group) for group in candidates) == 23
    )
    assert 2 * 105 * 7 + 2 * 23 * 7 == 1792


def test_candidate_generation_is_deterministic_and_matches_phase3a_hashes() -> None:
    spec = next(row for row in _rows() if is_wave1_spec(row))
    first = generate_candidates(spec)
    second = generate_candidates(spec)
    assert first == second
    baseline = json.loads(spec["baseline_candidate"])
    actual = next(row for row in first if row.candidate_role == "BASELINE")
    assert actual.candidate_id == baseline["candidate_id"]
    assert actual.config_hash == baseline["config_hash"]


def test_validation_eligibility_and_zero_trade_contract() -> None:
    base = {"validity_status": "VALID_RESULT", "turnover": 1.0, "execution_validation_status": True}
    assert validation_eligibility({**base, "trade_count": 5}) == (True, ())
    eligible, reasons = validation_eligibility({**base, "trade_count": 4})
    assert not eligible
    assert reasons == ("INSUFFICIENT_VALIDATION_TRADES",)
    eligible, reasons = validation_eligibility(
        {**base, "validity_status": "VALID_ZERO_TRADES", "trade_count": 0}
    )
    assert not eligible
    assert "ZERO_TRADES" in reasons


def test_selection_order_and_baseline_fallback() -> None:
    rows = [
        {
            "candidate_id": "b",
            "eligible": True,
            "return_1x": 1.0,
            "signed_global_be_bps": 2.0,
            "max_drawdown": -0.2,
            "turnover": 4.0,
        },
        {
            "candidate_id": "a",
            "eligible": True,
            "return_1x": 1.0,
            "signed_global_be_bps": 2.0,
            "max_drawdown": -0.1,
            "turnover": 5.0,
        },
    ]
    assert select_candidate(rows, "baseline")["selected_candidate_id"] == "a"
    fallback = select_candidate([], "baseline")
    assert fallback["selected_candidate_id"] == "baseline"
    assert fallback["selection_status"] == "BASELINE_FALLBACK"


def test_checkpoint_and_cache_keys_include_split_role_and_protocol() -> None:
    common = {
        "search_id": "s",
        "candidate_id": "c",
        "fold_id": "wf01",
        "split": "TRAIN",
        "evaluation_role": "CANDIDATE_PRESELECTION",
        "protocol_version": PROTOCOL_VERSION,
    }
    assert logical_checkpoint_id(**common) != logical_checkpoint_id(
        **{**common, "split": "VALIDATION"}
    )
    assert logical_checkpoint_id(**common) != logical_checkpoint_id(
        **{**common, "evaluation_role": "SELECTED_TEST"}
    )
    cache = {
        "strategy_id": "x",
        "search_id": "s",
        "config_hash": "h",
        "fold_id": "wf01",
        "split": "TEST",
        "timeframe": "1m",
        "lag": "lag1m",
        "premium_mode": "INCLUDED",
        "direction_mode": "ORIGINAL",
        "protocol_version": PROTOCOL_VERSION,
        "code_hash": "code",
        "data_provenance": "data",
    }
    assert physical_cache_key(**cache) != physical_cache_key(**{**cache, "split": "VALIDATION"})
    assert physical_cache_key(**cache) != physical_cache_key(
        **{**cache, "protocol_version": "other"}
    )


def test_selected_and_baseline_roles_can_share_physical_cache_but_not_logical_id() -> None:
    cache = {
        "strategy_id": "x",
        "search_id": "s",
        "config_hash": "h",
        "fold_id": "wf01",
        "split": "TEST",
        "timeframe": "1m",
        "lag": "lag1m",
        "premium_mode": "INCLUDED",
        "direction_mode": "ORIGINAL",
        "protocol_version": PROTOCOL_VERSION,
        "code_hash": "code",
        "data_provenance": "data",
    }
    assert physical_cache_key(**cache) == physical_cache_key(**cache)
    logical = {
        "search_id": "s",
        "candidate_id": "c",
        "fold_id": "wf01",
        "split": "TEST",
        "protocol_version": PROTOCOL_VERSION,
    }
    assert logical_checkpoint_id(
        **logical, evaluation_role="SELECTED_TEST"
    ) != logical_checkpoint_id(**logical, evaluation_role="BASELINE_TEST")


def test_walk_forward_folds_are_unchanged_and_chronological() -> None:
    protocol = json.loads((AUDIT / "phase3a_walk_forward_protocol.json").read_text())
    assert len(protocol["folds"]) == 7
    previous_test_end = None
    for fold in protocol["folds"]:
        assert fold["train"]["start_inclusive"] == "2021-07-01"
        assert fold["train"]["end_exclusive"] <= fold["validation"]["start_inclusive"]
        assert fold["validation"]["end_exclusive"] <= fold["test"]["start_inclusive"]
        if previous_test_end is not None:
            assert fold["test"]["start_inclusive"] == previous_test_end
        previous_test_end = fold["test"]["end_exclusive"]
