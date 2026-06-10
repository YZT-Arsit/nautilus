# Repository Cleanup Audit

> **Scope**: audit and conservative cleanup of the **strategy-framework layer**
> only. The upstream Nautilus-style codebase (Rust crates, `nautilus_trader`,
> build/governance config) is left intact. The low-level feature engine
> (`nautilus_ext/features/compute/`) is **not** modified.

---

## 1. Must keep — upstream / project infrastructure

These matter for build, license, security, linting, or dependency management and
must **not** be deleted even when they look irrelevant to internal usage.

| Path | Why |
|------|-----|
| `pyproject.toml`, `uv.lock` | Python build + locked deps |
| `Cargo.toml`, `Cargo.lock` | Rust build + locked deps |
| `Makefile`, `build.py` | build orchestration |
| `rust-toolchain.toml`, `rustfmt.toml`, `clippy.toml`, `deny.toml`, `tools.toml`, `osv-scanner.toml`, `taplo.toml` | Rust/toolchain + supply-chain config |
| `README.md` | project entry doc |
| `.github/`, `.pre-commit-config.yaml`, `.pre-commit-hooks/`, `.supply-chain/`, `.config/`, `.cargo/`, `.docker/` | CI, hooks, supply chain |
| linter configs (`.codecov.yml`, `.codespellrc`, `.markdownlint.jsonc`, `.typos.toml`, `.yamllint.yaml`, `.gitlint`, `.lycheeignore`, `.zizmor.yml`, `.dockerignore`, `.env.example`) | repo quality gates |
| `crates/`, `nautilus_trader/`, `python/`, `schema/`, `examples/` | upstream source / packages required by imports & tests |
| `quant_feature_engine/` | sibling internal package (out of scope here) |

## 2. Must keep — strategy-framework runtime (canonical)

| Path | Role |
|------|------|
| `run_strategy.py` (repo root) | the only normal user execution entry |
| `strategies/ma_crossover/{__init__,strategy}.py` | strategy definition + `PLUGIN` |
| `strategies/ma_crossover/{config,config_backtest,config_live_synthetic}.yaml` | run configs (colocated with strategy) |
| `strategies/ma_crossover/README.md` | per-strategy docs |
| `strategies/ma_crossover/sample_data/ma_crossover_bars.csv` | sample bars for the `csv_bars` config + tests |
| `strategy_framework/{__init__,plugin,registry,data_loaders,output,backtest,live_sources}.py` | reusable glue |
| `nautilus_ext/features/{api,builders,runner}.py` | stable public API + execution helper |
| `nautilus_ext/features/examples/synthetic_bars.py` | `BarEvent` + `make_bars()` |
| `nautilus_ext/features/compute/**` | low-level engine — **do not edit** |

## 3. Must keep — tests

| Test file | Covers |
|-----------|--------|
| `nautilus_ext/tests/test_compute_features.py` | feature compute correctness (404 tests) |
| `nautilus_ext/tests/test_ma_crossover.py` | strategy logic, config, builders, runner, registry, public-API boundary |
| `nautilus_ext/tests/test_execution_modes.py` | data loaders (synthetic/csv_bars/live_synthetic), `SignalRecorder`, output, run modes |
| `nautilus_ext/tests/test_top_level_structure.py` | top-level runner, plugin, registry, boundaries, wrapper, legacy-removal |
| other `nautilus_ext/tests/*` | feature data layer, ccxt connector/live, strategy interface |

## 4. Moved / merged (already done in prior migration)

| From (old `feature_strategies/`) | To |
|----------------------------------|----|
| `feature_strategies/run_strategy.py` | `run_strategy.py` (repo root, plugin-aware) |
| `feature_strategies/registry.py` | `strategy_framework/registry.py` (now `StrategyPlugin`-based) |
| `feature_strategies/data_loaders.py` | `strategy_framework/data_loaders.py` |
| `feature_strategies/output.py` | `strategy_framework/output.py` |
| `feature_strategies/backtest.py` | `strategy_framework/backtest.py` |
| `feature_strategies/live_sources.py` | `strategy_framework/live_sources.py` |
| `feature_strategies/strategies/ma_crossover.py` | `strategies/ma_crossover/strategy.py` (+ `PLUGIN`) |
| `feature_strategies/configs/*.yaml` | `strategies/ma_crossover/config*.yaml` |
| `feature_strategies/sample_data/*.csv` | `strategies/ma_crossover/sample_data/*.csv` |

## 5. Deleted

| Path | Reason | Verification | Replacement |
|------|--------|--------------|-------------|
| `feature_strategies/` (entire package, incl. transitional shims) | parallel/duplicate framework; superseded by root-level structure | `grep -rn feature_strategies` over code/tests/docs → only references are in this audit + a test asserting its removal; package was never committed (untracked) | `run_strategy.py`, `strategies/`, `strategy_framework/` |

A regression test (`test_legacy_feature_strategies_package_removed`) asserts the
package can no longer be imported, so it cannot silently return.

## 6. Generated / cache files

No cache or generated artifacts are **tracked** (`git ls-files | grep -E
'__pycache__|\.pyc|pytest_cache|mypy_cache|ruff_cache'` → 0). `.gitignore`
already covers `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`,
`.ruff_cache/`, `.coverage*`, `htmlcov/`, build dirs, `outputs/` subdirs, and ML
artefacts. The ~47 on-disk cache directories are all ignored and never enter the
repository, so no deletion from git is required.

## 7. Kept as legacy

| Path | Status |
|------|--------|
| `scripts/run_ma_crossover_demo.py` | thin wrapper → top-level `run_strategy.main(["--config", "strategies/ma_crossover/config.yaml"])`; path-robust for direct execution; covered by `test_scripts_wrapper_forwards` |

## 8. Conservative-deletion protocol followed

1. `grep`/`git ls-files` searched for every candidate before any removal.
2. Tests checked; references updated **before** deletion.
3. Stale doc references updated (this file, `ma_crossover_strategy_demo.md`,
   `strategy_framework/README.md`).
4. Runtime/test-referenced files were kept or replaced (wrapper / recreated
   sample CSV), never blindly removed.
5. Full test suite run after cleanup.

## 9. Outcome

- Canonical entry: `run_strategy.py` (repo root).
- Strategy code + config colocated under `strategies/<name>/`.
- Reusable glue under `strategy_framework/`.
- Low-level engine untouched under `nautilus_ext/features/compute/`
  (`features.py` not modified).
- `feature_strategies/` fully removed; only `scripts/run_ma_crossover_demo.py`
  remains as a compatibility wrapper.
