# Nautilus Core Cleanup Audit

**This audit deletes nothing.** It classifies the repository so future cleanup is
deliberate. The headline decision: **the original Nautilus Trader core is
preserved** as an optional future backtest/live backend (see
[NAUTILUS_INTEGRATION_BOUNDARY.md](NAUTILUS_INTEGRATION_BOUNDARY.md)).

## A. Must keep — our custom framework

| Path | Role |
|------|------|
| `run_strategy.py` | canonical root-level user entry point |
| `strategies/` | user-facing strategy definitions + configs |
| `strategy_framework/` | orchestration: plugin, registry, output, backtest recorder, **backends/** |
| `data_engine/` | canonical data processing package |
| `feature_engine/` | canonical feature processing package |
| `nautilus_ext/features/` | compatibility shim re-exporting `feature_engine` |

## B. Must keep — for future Nautilus backend (do NOT delete now)

Even though the MA crossover demo does not use them today, these are required for
the planned optional Nautilus execution/backtest backend:

- `nautilus_trader/` backtest engine
- `nautilus_trader/` live engine
- execution / orders / portfolio / risk
- data engine / catalog
- adapters
- model objects (core data/value types)
- core build/package files (`pyproject.toml`, `Cargo.toml`, `Cargo.lock`,
  `uv.lock`, Rust crates) and the repository's required tests

**Reason:** removing these forfeits the future Nautilus backend path. Keep until
explicitly decided otherwise.

## C. Safe cleanup candidates (mechanical, low risk)

- Python/byte caches: `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`,
  `.ruff_cache/`
- build/generated artifacts not under version control
- stale duplicate `feature_strategies/` paths **if** superseded — *status: the
  legacy `feature_strategies/` package is already removed; a boundary test
  asserts it stays gone.*
- duplicate/abandoned configs (none identified at audit time)
- old demo scripts that are **not** wrappers and are unreferenced (none
  identified — `scripts/run_ma_crossover_demo.py` is an active wrapper, see D)

These may be cleaned with normal hygiene (caches are already git-ignored). This
audit does not remove them automatically.

## D. Keep as compatibility wrappers

| Path | Why |
|------|-----|
| `scripts/run_ma_crossover_demo.py` | forwards to `run_strategy.py`; covered by a test |
| `strategy_framework/data_loaders.py` | thin re-export of `data_engine.loader` |
| `nautilus_ext/features/` (whole package) | shim re-exporting the canonical `feature_engine` |
| `feature_strategies/*` | **N/A** — already removed; only would apply if old tests/docs still referenced it |

## E. Do NOT delete without explicit approval

- source core (custom **and** Nautilus)
- tests (custom suite under `nautilus_ext/tests/` and Nautilus's own tests)
- build/package metadata (`pyproject.toml`, `Cargo.toml`, lockfiles)
- docs with architecture value (this file, the boundary doc, feature-engine
  report, strategy demo docs)
- Nautilus core modules (category B)

## Summary

- No destructive action is taken by this audit.
- Nautilus Trader core is **retained** as an optional future backend.
- Only caches/generated artifacts are clearly safe to remove, and only via normal
  hygiene — not by this document.
