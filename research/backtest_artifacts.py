"""Stable ``run_uid`` + artifact identity helpers for backtest traceability.

Every backtest experiment gets ONE deterministic ``run_uid`` derived purely from
experiment-identifying fields (strategy, symbol, venue, window, sizing mode,
params hash, data version, engine). It never depends on file modification times
or random numbers, so re-deriving for the same experiment always yields the same
id -- the anchor that lets every table row, PnL series, chart, and raw run dir
line up.

Pure stdlib. No network, no backtest, no strategy import, no file writes here.

run_uid shape (human-readable prefix + short stable hash suffix)::

    VWM_BTCUSDT_BINANCE_futures_um_15m_20260301_20260531_vol_targeted_a13f92

The readable prefix is for humans; the 6-hex suffix is a hash over the *full*
canonical key (including params_hash / data_version / strategy_version /
backtest_engine / contract_type) so two runs that share the prefix but differ in
params still get distinct ids.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

UNKNOWN = "unknown"

# Canonical field order. Used both for the hash key and for the manifest.
RUN_KEY_FIELDS = (
    "strategy_name", "strategy_version", "symbol", "exchange", "venue_type",
    "contract_type", "bar_type", "start", "end", "sizing_mode", "params_hash",
    "data_version", "backtest_engine",
)


def stable_hash(text: str, length: int = 6) -> str:
    """Deterministic short hex digest (sha256). No randomness, no salt."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _norm(value) -> str:
    if value is None:
        return UNKNOWN
    s = str(value).strip()
    return s if s else UNKNOWN


def _compact_date(value) -> str:
    """``2026-03-01`` -> ``20260301``; keeps only digits, ``unknown`` if none."""
    digits = "".join(ch for ch in _norm(value) if ch.isdigit())
    return digits or UNKNOWN


def canonical_run_key(fields: dict) -> str:
    """Order-stable ``key=value|...`` string over RUN_KEY_FIELDS (normalized)."""
    return "|".join(f"{k}={_norm(fields.get(k))}" for k in RUN_KEY_FIELDS)


def build_run_uid(fields: dict) -> str:
    """Human-readable prefix + 6-hex stable hash over the full canonical key."""
    suffix = stable_hash(canonical_run_key(fields))
    parts = [
        _norm(fields.get("strategy_name")).upper(),
        _norm(fields.get("symbol")).upper(),
        _norm(fields.get("exchange")).upper(),
        _norm(fields.get("venue_type")),
        _norm(fields.get("bar_type")),
        _compact_date(fields.get("start")),
        _compact_date(fields.get("end")),
        _norm(fields.get("sizing_mode")),
        suffix,
    ]
    return "_".join(parts)


def window_label(start: str, end: str) -> str:
    """``2026-03-01`` / ``2026-05-31`` -> ``2026Q2`` (quarter of the END month)."""
    e = _norm(end)
    if len(e) >= 7 and e[:4].isdigit() and e[5:7].isdigit():
        return f"{e[:4]}Q{(int(e[5:7]) - 1) // 3 + 1}"
    return e


def artifact_id(strategy: str, symbol: str, bar_type: str, win_label: str,
                sizing_mode: str) -> str:
    """Readable Phase-1 id (no hash). Kept for back-compat with the earlier index."""
    return f"{_norm(strategy).upper()}_{_norm(symbol).upper()}_{_norm(bar_type)}_{win_label}_{_norm(sizing_mode)}"


# --- params_hash resolution -------------------------------------------------

def resolve_params_hash(summary: dict | None = None,
                        config_resolved_text: str | None = None) -> tuple[str, str]:
    """Return ``(params_hash, source)``.

    Preference order: the ``params_hash`` already recorded in summary.json (the
    runner's own canonical strategy-param hash) -> a stable hash of the resolved
    config text -> ``unknown``. ``source`` records which path was taken so the
    manifest can flag low-confidence ids.
    """
    if summary:
        ph = summary.get("params_hash")
        if ph not in (None, "", "NA"):
            return str(ph), "summary"
    if config_resolved_text:
        return stable_hash(config_resolved_text, 12), "config_resolved"
    return UNKNOWN, "missing"


# --- identity object --------------------------------------------------------

@dataclass
class RunIdentity:
    strategy_name: str
    strategy_version: str
    symbol: str
    exchange: str
    venue_type: str
    contract_type: str
    bar_type: str
    start: str
    end: str
    sizing_mode: str
    params_hash: str
    data_version: str
    backtest_engine: str
    params_hash_source: str = "missing"
    missing_fields: tuple[str, ...] = field(default_factory=tuple)

    def as_key_fields(self) -> dict:
        return {k: getattr(self, k) for k in RUN_KEY_FIELDS}

    @property
    def run_uid(self) -> str:
        return build_run_uid(self.as_key_fields())

    @property
    def window_label(self) -> str:
        return window_label(self.start, self.end)

    @property
    def artifact_id(self) -> str:
        return artifact_id(self.strategy_name, self.symbol, self.bar_type,
                           self.window_label, self.sizing_mode)


_CONTRACT_BY_VENUE = {
    "futures_um": "perpetual", "futures_cm": "perpetual_coin", "spot": "spot",
}


def contract_type_for(venue_type: str) -> str:
    return _CONTRACT_BY_VENUE.get(_norm(venue_type), UNKNOWN)


def build_identity(summary: dict, *, strategy: str, sizing_mode: str,
                   bar_type: str, start: str, end: str,
                   strategy_version: str = "v1", data_version: str = UNKNOWN,
                   backtest_engine: str = "nautilus_backtest",
                   config_resolved_text: str | None = None) -> RunIdentity:
    """Build a :class:`RunIdentity` from a summary dict + run-level overrides.

    Fields absent from the summary fall back to ``unknown`` and are recorded in
    ``missing_fields`` so the manifest can mark them (the run_uid still stays
    stable for that incomplete-but-consistent input).
    """
    exchange = _norm(summary.get("exchange"))
    venue_type = _norm(summary.get("venue_type"))
    symbol = _norm(summary.get("symbol"))
    contract_type = contract_type_for(venue_type)
    params_hash, ph_source = resolve_params_hash(summary, config_resolved_text)

    resolved = {
        "strategy_name": _norm(strategy), "strategy_version": _norm(strategy_version),
        "symbol": symbol, "exchange": exchange, "venue_type": venue_type,
        "contract_type": contract_type, "bar_type": _norm(bar_type),
        "start": _norm(start), "end": _norm(end), "sizing_mode": _norm(sizing_mode),
        "params_hash": params_hash, "data_version": _norm(data_version),
        "backtest_engine": _norm(backtest_engine),
    }
    missing = tuple(k for k, v in resolved.items() if v == UNKNOWN)
    return RunIdentity(params_hash_source=ph_source, missing_fields=missing, **resolved)


# --- path helpers -----------------------------------------------------------

def pnl_filename(run_uid: str) -> str:
    return f"{run_uid}_pnl.csv"


def chart_filename(run_uid: str, kind: str) -> str:
    return f"{run_uid}_{kind}.png"


CHART_KINDS = ("equity_curve", "drawdown", "pnl_curve", "position", "benchmark_comparison")


def rel_path(path) -> str:
    """Posix-normalized string (stable across Windows/Unix in the manifests)."""
    return str(path).replace("\\", "/")
