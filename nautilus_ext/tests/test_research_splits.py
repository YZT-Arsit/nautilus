"""Tests for research/splits.py (pure-Python, no pandas)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.splits import (  # noqa: E402
    DEFAULT_SPLITS,
    assign_splits,
    purge_mask,
    split_of_ts,
    split_summary,
    validate_splits,
)


def _ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


# --- 1. date assignment -----------------------------------------------------

def test_split_assignment_by_date():
    assert split_of_ts(_ts(2024, 7, 1)) == "train"
    assert split_of_ts(_ts(2025, 12, 31)) == "train"
    assert split_of_ts(_ts(2026, 1, 1)) == "val"
    assert split_of_ts(_ts(2026, 4, 30)) == "val"
    assert split_of_ts(_ts(2026, 5, 1)) == "test"
    assert split_of_ts(_ts(2026, 6, 16)) == "test"
    assert split_of_ts(_ts(2024, 1, 1)) is None     # before train
    assert split_of_ts(_ts(2026, 7, 1)) is None      # after test


# --- 2. non-overlap ---------------------------------------------------------

def test_default_splits_do_not_overlap():
    validate_splits()  # must not raise


def test_overlapping_splits_raise():
    bad = {"a": ("2024-01-01", "2024-06-30"), "b": ("2024-06-01", "2024-12-31")}
    with pytest.raises(ValueError, match="overlap"):
        validate_splits(bad)


# --- 3. boundary purge ------------------------------------------------------

def test_purge_when_label_horizon_crosses_split():
    # row in train, horizon bar in val -> purge True; same split -> False.
    row_splits = ["train", "train"]
    horizon_ts = [_ts(2026, 1, 2), _ts(2025, 12, 1)]  # 1st crosses into val, 2nd stays in train
    mask = purge_mask(row_splits, horizon_ts)
    assert mask == [True, False]


def test_purge_none_horizon_not_dropped_here():
    # rows without a horizon bar are handled as horizon-invalid by labels, not purge.
    assert purge_mask(["train"], [None]) == [False]


# --- 4. test split assignment + summary ------------------------------------

def test_assign_splits_and_summary():
    ts = [_ts(2024, 7, 1), _ts(2026, 2, 1), _ts(2026, 6, 1), _ts(2030, 1, 1)]
    splits = assign_splits(ts)
    assert splits == ["train", "val", "test", None]
    summary = split_summary(splits)
    assert summary == {"train": 1, "val": 1, "test": 1, "none": 1}


def test_default_splits_constant_shape():
    assert set(DEFAULT_SPLITS) == {"train", "val", "test"}
