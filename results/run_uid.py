"""Deterministic ``run_uid`` — the anchor for results reuse.

Every experiment gets ONE id derived purely from experiment-identifying fields
(strategy, symbol, venue, window, fee, params hash, engine). No file mtimes, no
randomness — re-deriving for the same experiment always yields the same id, so
tables, PnL series, charts and raw run dirs all line up. Pure stdlib.

Shape (human-readable prefix + short stable hash)::

    VWM_BTCUSDT_BINANCE_1m_20260301_20260531_nofee_a13f92
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

UNKNOWN = "unknown"

# Canonical field order for the hash key.
RUN_KEY_FIELDS = (
    "strategy", "strategy_version", "symbol", "exchange", "venue_type",
    "bar_type", "start", "end", "fee", "params_hash", "data_version", "engine",
)


def stable_hash(text: str, length: int = 6) -> str:
    """Deterministic short hex digest (sha256). No randomness, no salt."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def params_hash(params: dict[str, Any]) -> str:
    """Stable 12-hex hash of a params dict (JSON, sorted keys)."""
    return stable_hash(json.dumps(params, sort_keys=True, default=str), 12)


def _norm(value: Any) -> str:
    if value is None:
        return UNKNOWN
    s = str(value).strip()
    return s or UNKNOWN


def _compact_date(value: Any) -> str:
    digits = "".join(ch for ch in _norm(value) if ch.isdigit())
    return digits or UNKNOWN


def canonical_run_key(fields: dict) -> str:
    """Order-stable ``key=value|...`` string over RUN_KEY_FIELDS (normalized)."""
    return "|".join(f"{k}={_norm(fields.get(k))}" for k in RUN_KEY_FIELDS)


def build_run_uid(fields: dict) -> str:
    """Human-readable prefix + 6-hex stable hash over the full canonical key."""
    suffix = stable_hash(canonical_run_key(fields))
    parts = [
        _norm(fields.get("strategy")).upper(),
        _norm(fields.get("symbol")).upper(),
        _norm(fields.get("exchange")).upper(),
        _norm(fields.get("bar_type")),
        _compact_date(fields.get("start")),
        _compact_date(fields.get("end")),
        _norm(fields.get("fee")),
        suffix,
    ]
    return "_".join(p for p in parts if p != UNKNOWN or p == parts[-1])


def window_label(end: str) -> str:
    """``2026-05-31`` -> ``2026Q2`` (quarter of the END month)."""
    e = _norm(end)
    if len(e) >= 7 and e[:4].isdigit() and e[5:7].isdigit():
        return f"{e[:4]}Q{(int(e[5:7]) - 1) // 3 + 1}"
    return e


__all__ = [
    "RUN_KEY_FIELDS",
    "stable_hash",
    "params_hash",
    "canonical_run_key",
    "build_run_uid",
    "window_label",
]
