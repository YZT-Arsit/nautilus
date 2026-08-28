from __future__ import annotations

import numpy as np

from scripts.internal.build_phase6b_cost_episode_audit import canonical_drawdown
from scripts.internal.build_phase6b_cost_episode_audit import primary_label
from scripts.internal.build_phase4b_cost_episode_audit import cost_adjusted_increments
from scripts.internal.build_phase4b_cost_episode_audit import exact_be


def _row(**updates):
    row = {
        "episode_count": 100,
        "SURVIVES_0_10_BPS": True,
        "episode_BE_median": 1.0,
        "episode_BE_positive_fraction": 0.6,
        "LOPO_0_10": True,
        "Return_without_top5pct": 0.1,
        "BE_without_top5pct": 0.5,
        "winner_concentrated": True,
    }
    row.update(updates)
    return row


def test_cost_accounting_and_break_even_identity():
    gross = np.array([0.01, -0.003, 0.004])
    turnover = np.array([1.0, 0.5, 1.5])
    be = exact_be(float(gross.sum()), float(turnover.sum()))
    assert abs(float(cost_adjusted_increments(gross, turnover, be).sum())) < 1e-14


def test_primary_label_is_strict_and_deterministic():
    assert primary_label(_row())[0] == "ECONOMICALLY_STRONG"
    assert primary_label(_row(SURVIVES_0_10_BPS=False))[0] == "COST_FRAGILE"
    assert primary_label(_row(episode_BE_median=-0.1))[0] == "EPISODE_FRAGILE"
    assert primary_label(_row(LOPO_0_10=False, Return_without_top5pct=-0.1, BE_without_top5pct=-1.0))[0] == "TEMPORALLY_FRAGILE"


def test_phase5_and_prior_drawdown_conventions_are_explicit():
    increments = np.array([-0.1, 0.2, -0.05])
    assert canonical_drawdown(increments, "PHASE5A") <= 0
    assert canonical_drawdown(increments, "PRE_PHASE5") <= 0


def test_zero_episode_is_evidence_warning_not_silent_drop():
    assert primary_label(_row(episode_count=0))[0] == "INSUFFICIENT_EPISODE_EVIDENCE"
