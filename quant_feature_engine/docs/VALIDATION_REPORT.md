# quant_feature_engine — Status & Validation

**Last verified:** 2026-05-28 on `D:\nautilus` (Windows 10, Python 3.13.13,
polars 1.41.1, pyarrow 24.0.0).
**Test suite:** 39/39 unit tests pass.
**Real-data parity:** confirmed for `IH2303.CFFEX` and `IF2301.CFFEX`
(`2023-01-03` only) across 7 chunk sizes within `1e-6` tolerance.
**Not yet production-ready** — see [§7 Production-readiness gaps](#7-production-readiness-gaps)
and [§8 Production backlog](#8-production-backlog) for the concrete work
required before the framework can be relied on in a live trading or research
pipeline.

This document is the evidence trail for what has been verified, the environment
it was verified in, and the explicit list of work still required before the
framework is production-grade.

---

## 1. Tested environment

| Component | Value |
|---|---|
| Host | `DESKTOP-4QM8PFQ` (Windows 10 AMD64) |
| Working tree | `D:\nautilus` |
| Python | 3.13.13 (from `D:\nautilus\.venv\Scripts\python.exe`; parent miniconda 3.13.12) |
| uv | 0.11.8 |
| pip | 26.0.1 |
| polars | 1.41.1 |
| polars-runtime-32 | 1.41.1 |
| pyarrow | 24.0.0 |
| numpy | 2.4.4 |
| pytest | 7.4.4 |
| pyyaml | 6.0.3 |

Local cross-check environment (parity test): macOS 25.5.0 ARM64, Python 3.13.3,
polars 1.41.0, pyarrow 24.0.0, numpy 2.4.6. The framework behaves identically
across the two platforms — the only OS-specific issue surfaced was a test that
asserted POSIX path separators; that was fixed (see §5).

---

## 2. Validation matrix

| Layer | Scope | Result | Evidence |
|---|---|---|---|
| **Unit tests** | DAG resolution, feature registry, storage layout, manifest dedup, every concrete feature's smoke + snapshot round-trip, **batch ≡ streaming parity** at chunk sizes 1/7/50/500 over synthetic 2-symbol data, **end-to-end 2-symbol × 2-date** parity, EOD archiver idempotency | **39 passed** | §4.1 |
| **Synthetic MVP harness** | `scripts/validate_qfe_mvp.py` — 2 symbols × 2 dates × 240 minute bars, chunk size 17, all 8 feature columns | **PASS** (exit 0) | §4.2 |
| **Real-data parity (one chunk size)** | IH2303.CFFEX, 1 day, chunk=17 | **PASS** | §4.3 |
| **Real-data parity (two chunk sizes)** | IF2301.CFFEX, 1 day, chunks=17,113 | **PASS** | §4.4 |
| **Real-data chunk-boundary stress** | IF2301.CFFEX, 1 day, chunks=1,20,26,30,60,121,242 | **PASS** | §4.5 |

All real-data runs compared offline backfill output against streaming-replay
output **row by row, column by column, within a 1e-6 tolerance** across the
8 features the framework currently emits (`sma_20, vol_30, rsi_14, macd,
macd_signal, macd_hist, vwm_20, vwm_zscore_60`).

---

## 3. Validation scope (what the current evidence covers)

| Verified | Not verified |
|---|---|
| Offline backfill and streaming replay produce identical features (within `1e-6`) for real catalog data on Windows. | Live ingestion (latency, jitter, back-pressure, concurrent writers). |
| Per-symbol state isolation in `PerSymbolMixin`. | Multi-symbol streaming on real data (only synthetic). |
| Schema stability across micro-batch boundaries aligned to feature window lengths (1, 20, 26, 30, 60, 121, 242). | Cross-day rolling windows on real data (catalog has 1 day). |
| Hive-Parquet partition pruning + column pruning via `ParquetStore`. | Network filesystems / cloud object stores; file-system limits. |
| Manifest dedup is idempotent on re-run. | Concurrent-writer safety on the manifest. |
| EOD archiver stage-then-commit on a happy-path single-day synthetic run. | EOD archiver under crash injection, partial writes, multi-day data, or Windows ACL edge cases. |
| Feature output values agree to `1e-6` against an offline baseline. | Whether feature values are predictive of returns. That is a research question, not a framework question. |

---

## 4. Detailed evidence

### 4.1 Unit test suite — final run

Commands (server):

```powershell
ssh quant_data@172.16.112.81 \
    "powershell -NoProfile -Command 'cd D:\nautilus; \
        .\.venv\Scripts\python.exe -m pytest quant_feature_engine\tests -q'"
```

Output:

```
.......................................                                  [100%]
39 passed in 22.07s
```

Coverage (`quant_feature_engine/tests/`):

- `test_dag.py` — 4 tests (topo order, level grouping, transitive deps, unknown-feature error)
- `test_features.py` — 7 tests (smoke per feature, derived-feature run, snapshot round-trip)
- `test_storage.py` — 3 tests (partition path OS-portability, parquet round-trip, manifest dedup)
- `test_streaming_batch_parity.py` — 20 parametric tests (4 chunk sizes × 5 features)
- `test_end_to_end.py` — 5 tests (offline write, manifest skip, no cross-symbol contamination, **2 symbols × 2 dates parity**, archiver idempotency)

### 4.2 Synthetic MVP harness

Commands (server):

```powershell
ssh quant_data@172.16.112.81 \
    "powershell -NoProfile -Command 'cd D:\nautilus; \
        .\.venv\Scripts\python.exe scripts\validate_qfe_mvp.py'"
```

Output (abridged):

```
OS:           Windows 10 (AMD64)
Python:       3.13.13
polars:       1.41.1
pyarrow:      24.0.0
numpy:        2.4.4

[step 0/4] writing synthetic OHLCV ... 2 symbols × 2 dates × 240 bars = 960 rows
[step 1/4] offline backfill: 2 partitions, 6 features ...
           offline rows: 960, manifest rows: 12
[step 2/4] streaming replay: chunk size 17 rows ...
           2026-05-25: batches=29 rows=480 errors=0
           2026-05-26: batches=29 rows=480 errors=0
[step 3/4] comparing offline vs streaming (tol=1e-06) ...
           [OK ] 2026-05-25: rows=480  reason=ok
           [OK ] 2026-05-26: rows=480  reason=ok
PASS — offline backfill and streaming replay produce identical features
```

Exit code: `0`.

### 4.3 Real-data validation — first instrument (IH2303.CFFEX)

Bridge build:

```powershell
.\.venv\Scripts\python.exe internal_examples\build_qfe_raw_from_catalog.py \
    --catalog D:\QuanHub\DataHome\DataTrans\nautilus_catalog \
    --instrument-id IH2303.CFFEX \
    --output-root D:\nautilus\data\raw \
    --asset-class futures --exchange CFFEX --frequency 1m
```

- 16,125 quote ticks → 241 minute bars (`volume_type=synthetic_tick_count`)
- Wrote one partition: `asset_class=futures/exchange=CFFEX/frequency=1m/trading_date=2023-01-03`

Validation:

```powershell
.\.venv\Scripts\python.exe scripts\validate_qfe_real_data.py \
    --raw-root D:\nautilus\data\raw --instrument-id IH2303.CFFEX \
    --asset-class futures --exchange CFFEX --frequency 1m
```

Result: `PASS — features match across 1 trading_date(s) for IH2303.CFFEX`
(chunk=17, 15 batches, 0 errors). Exit code `0`.

### 4.4 Real-data validation — densest instrument (IF2301.CFFEX), two chunk sizes

Bridge build (24,489 ticks → 242 minute bars), then:

```powershell
.\.venv\Scripts\python.exe scripts\validate_qfe_real_data.py \
    --raw-root D:\nautilus\data\raw --instrument-id IF2301.CFFEX \
    --chunk-sizes 17,113
```

| Chunk size | Batches | Rows | Errors | Compare |
|---:|---:|---:|---:|---|
| 17 | 15 | 242 | 0 | **OK** |
| 113 | 3 | 242 | 0 | **OK** |

Verdict: `PASS — features match for IF2301.CFFEX across 1 trading_date(s) and 2
chunk size(s)`. Exit code `0`.

### 4.5 Chunk-boundary stress sweep — IF2301.CFFEX, 7 chunk sizes

Chunk sizes deliberately chosen to land on/near feature windows:

```powershell
.\.venv\Scripts\python.exe scripts\validate_qfe_real_data.py \
    --raw-root D:\nautilus\data\raw --instrument-id IF2301.CFFEX \
    --chunk-sizes 1,20,26,30,60,121,242
```

| chunk_size | batches | rows | errors | compare | rationale |
|---:|---:|---:|---:|---|---|
| 1   | 242 | 242 | 0 | **OK** | tick-by-tick worst case |
| 20  |  13 | 242 | 0 | **OK** | exact `sma_20.window` |
| 26  |  10 | 242 | 0 | **OK** | exact `macd.window` |
| 30  |   9 | 242 | 0 | **OK** | exact `vol_30.window` |
| 60  |   5 | 242 | 0 | **OK** | exact `vwm_zscore_60.window` |
| 121 |   2 | 242 | 0 | **OK** | half-day |
| 242 |   1 | 242 | 0 | **OK** | full-day in one batch |

All 7 produce identical features (within `1e-6`) on all 8 columns across all
242 rows. Final verdict: `PASS — features match for IF2301.CFFEX across 1
trading_date(s) and 7 chunk size(s)`. Exit code `0`.

---

## 5. Issues found and fixed during validation

| Issue | Category | Fix | Where |
|---|---|---|---|
| `__init__.py` eagerly imported polars-using modules | Design | PEP 562 lazy attribute access | [core/feature.py](../__init__.py) |
| `Manifest.read()` only read coalesced file, missed shards | Bug | Glob all `manifest*.parquet` | [storage/metadata.py](../storage/metadata.py) |
| `ParquetStore.scan()` unioned schemas across heterogeneous partitions | Bug | Narrow dataset root when filter fully specifies partition cols; drop partition cols from output | [storage/parquet_store.py](../storage/parquet_store.py) |
| RSI/MACD output dtype was `Null` on all-null warm-up chunk | Bug | Pin `pl.Float64` explicitly | [features/rsi.py](../features/rsi.py), [features/macd.py](../features/macd.py) |
| Features saw upstream-feature columns in their saved tail buffers | Bug | Project input frame to `meta.inputs` before each `update()` call | [streaming/engine.py](../streaming/engine.py), [execution/batch_engine.py](../execution/batch_engine.py) |
| Streaming output rows reordered when symbols interleaved | Bug | Tag rows with `__qfe_row_idx__` in `PerSymbolMixin.process_per_symbol` and re-sort | [core/feature.py](../core/feature.py) |
| Test `test_partition_path_round_trip` asserted POSIX separators | Test logic | Use `os.sep.join([...])` | [tests/test_storage.py](../tests/test_storage.py) |
| Cross-day state leakage was implicit | Design | Added `cross_day` policy to `FeatureMeta`; reset-per-symbol-when-day-changes in mixin | [core/feature.py](../core/feature.py) |
| Composite state-store key (feature, version, params_hash, freq, session, symbol) | Design | New `StateScope` dataclass + `state_key()` helper | [core/state.py](../core/state.py) |
| EOD archiver could leave partial writes on crash | Design | Stage-then-commit with atomic rename; manifest committed last; `mode={"error","append","overwrite"}` | [streaming/archiver.py](../streaming/archiver.py) |

All issues were caught **before** real-data validation. The real-data sweep
itself surfaced no new framework bugs.

---

## 6. Reproduction — full command sequence

From a fresh checkout of `nautilus_trader` on Windows:

```powershell
# 0. (one time) — install qfe runtime deps into the project's existing .venv
cd D:\nautilus
.\.venv\Scripts\python.exe -m pip install -r quant_feature_engine\requirements.txt

# 1. Unit tests
.\.venv\Scripts\python.exe -m pytest quant_feature_engine\tests -q

# 2. Synthetic MVP validation
.\.venv\Scripts\python.exe scripts\validate_qfe_mvp.py

# 3. Catalog inventory (read-only; writes CSV)
.\.venv\Scripts\python.exe scripts\scan_cffex_catalog.py `
    --catalog D:\QuanHub\DataHome\DataTrans\nautilus_catalog `
    --output-dir D:\nautilus\outputs\qfe_catalog_inventory

# 4. Build minute bars for one instrument
.\.venv\Scripts\python.exe internal_examples\build_qfe_raw_from_catalog.py `
    --catalog D:\QuanHub\DataHome\DataTrans\nautilus_catalog `
    --instrument-id IF2301.CFFEX `
    --output-root D:\nautilus\data\raw `
    --asset-class futures --exchange CFFEX --frequency 1m

# 5. Real-data parity (single chunk size)
.\.venv\Scripts\python.exe scripts\validate_qfe_real_data.py `
    --raw-root D:\nautilus\data\raw `
    --instrument-id IF2301.CFFEX `
    --asset-class futures --exchange CFFEX --frequency 1m

# 6. Chunk-boundary stress sweep
.\.venv\Scripts\python.exe scripts\validate_qfe_real_data.py `
    --raw-root D:\nautilus\data\raw `
    --instrument-id IF2301.CFFEX `
    --asset-class futures --exchange CFFEX --frequency 1m `
    --chunk-sizes 1,20,26,30,60,121,242
```

Each script exits `0` on success, `1` on validation failure — suitable for CI.

---

## 7. Production-readiness gaps

These are the items that must be closed before this framework is suitable for
production use. They are not aspirational improvements; each is a known risk
or operational hole.

### 7.1 Data-coverage gaps (block real-data trust)

| Gap | Risk | Closure requires |
|---|---|---|
| Single trading date in the catalog. | Cross-day rolling-window and `cross_day="reset"` paths are untested on real data. | More days in the upstream catalog; not a code change. |
| Volume is synthetic tick count. | Any volume-weighted feature is engineering-valid only. Production strategy decisions would be wrong. | Ingest real trade-tick feed, or extend `TickToBarAggregator` with `volume_mode="trade_size"`. |
| Session-start `01:29 UTC` bar is pre-open. | First-minute features per day are computed from ~30s of pre-open quotes. | Document the filter in feature consumers; do not change the aggregator. |

### 7.2 Reproducibility gaps

| Gap | Risk | Closure requires |
|---|---|---|
| `requirements.txt` uses floors (`polars>=0.20`). | Two installs days apart may resolve to different versions, producing different binary output. | Pin exact versions and ship a lockfile (`uv lock` or `pip-compile`). |
| No checksum on input or output Parquet files. | Silent data drift goes undetected. | Add SHA256 to manifest rows; compare on read in `--strict` mode. |
| `validate_qfe_real_data.py` writes features to a temp dir each run. | Re-runs are not comparable; cannot diff today vs yesterday. | Optional `--output-dir` (already implemented); add `--baseline-dir` flag that diffs against a frozen baseline. |
| No deterministic seed for synthetic test data. | Currently fixed (seed=42 in `_make_day`), but no test asserts that the synthetic frame's hash matches a known value. | Add a `test_synthetic_baseline_unchanged` test pinning the SHA256 of the generated frame. |

### 7.3 Remote / local consistency gaps

| Gap | Risk | Closure requires |
|---|---|---|
| Sync to server is manual `scp`. | Local and server can drift; no audit trail; easy to forget a file. | Install git on the server (one-time `winget install Git.Git`), then sync via `git fetch origin && git checkout <branch>` per SKILL.md Mode A. |
| Install on server was triggered as `pip install -r ...`. | Editable Nautilus install path is fragile; no record of what was installed. | Write `scripts/install_qfe.ps1` (and `.sh` for Linux); commit and run the same script on both ends. |
| No automated check that local files = server files after sync. | Drift is invisible. | Add `scripts/verify_remote_parity.ps1` that hashes every `quant_feature_engine/*.py` on both sides and diffs. |
| SSH key + host alias setup is undocumented. | Anyone new to the project re-learns it. | Add a `docs/REMOTE_ACCESS.md` (no secrets) covering: key generation, where to install on the server, SSH alias, fallback to password. |

### 7.4 Maintainability gaps

| Gap | Risk | Closure requires |
|---|---|---|
| No CI. Tests only run when someone remembers. | Regressions ship to the server unnoticed. | GitHub Actions / equivalent: pytest on Linux and Windows, on every PR. Block merges on failure. |
| No type checker, no lint, no formatter check. | Drift in code style; type errors land in production. | Add `mypy`, `ruff`, `ruff format` to CI. |
| No coverage threshold. | A new module can land with zero tests. | `pytest --cov --cov-fail-under=80` (current measured coverage unknown — first measure, then set a floor). |
| Logging is unstructured `logger.info(...)` calls. | Hard to grep in production; no structured fields for dashboards. | Move to `structlog` or `logging.config` JSON formatter for production deployments. |

### 7.5 Operational gaps (only relevant once the framework runs in a live process)

| Gap | Risk | Closure requires |
|---|---|---|
| `StreamingEngine.stats` is in-memory only. | No metrics visible to ops. | Wire `stats.batches`/`rows`/`errors`/`checkpoints` to Prometheus / equivalent. |
| `errors` counter increments but no alert threshold is defined. | Silent failure of a feature update goes unnoticed. | Define error-budget SLO (e.g. "<0.1% of batches may error"); wire alert. |
| Manifest grows append-only until 32 shards trigger compaction. | Over months: manifest scan becomes slow. | Scheduled compaction job; document the cadence. |
| Redis state store is optional and not health-checked. | Streaming engine restart with stale/missing state silently re-warms wrong. | Health check on engine startup: verify the expected checkpoint exists and is recent. Fail loudly if not. |
| No runbook for: streaming engine error, EOD archive failure, Redis unreachable, manifest corruption, feature-version bump rollback. | On-call has no playbook. | `docs/RUNBOOK.md` with one section per failure mode. |
| No capacity plan. | Don't know if storage / compute will scale. | Project ticks-per-day × feature-bytes-per-row × 252 days × N years; document the projected disk + RAM. |
| Security / file-permission review never done. | Catalog reads with whatever permissions the SSH user has; no segregation between read-only catalog and writable feature store. | Document principle-of-least-privilege; ensure the service user can read catalog but write only `data/features/` and `data/_meta/`. |

### 7.6 Engineering-correctness gaps (small, but real)

| Gap | Risk | Closure requires |
|---|---|---|
| Streaming engine swallows per-batch exceptions and continues (`stats.errors++`). | A persistently failing batch produces nulls forever with no escalation. | Add an `errors_per_minute` alert + an option to halt after N consecutive failures. |
| `EodArchiver` leaves the staging directory on failure for forensics but never cleans old ones. | Disk fills up over time on a flaky link. | Periodic cleanup job; or TTL on the staging root. |
| Tolerance is `1e-6` — generous compared to observed bit-identity. | Future polars upgrade could regress by `~1e-12` and we wouldn't notice. | Add a strict-mode pytest run with tolerance `0.0`. |
| Parity test sweeps 4 chunk sizes; real-data sweep covers 7. CI runs none of the real-data sweep. | Real-data regressions caught only when humans remember. | Pin a small real-data fixture (synthesised, not from the protected catalog) and add it to CI. |

---

## 8. Production backlog

Each item below has a concrete deliverable, an effort estimate, and a clear
acceptance criterion. None require Ray. They are listed in suggested
priority order — please confirm or reorder before I start any of them.

### Priority A — must close before any live use

| # | Deliverable | Effort | Acceptance criterion |
|---|---|---|---|
| A1 | **Pinned dependency lockfile.** Replace `requirements.txt` floors with exact pins; ship `uv.lock` or `requirements.lock.txt`. | 0.5 day | Two fresh installs on the same OS produce byte-identical `.venv`. |
| A2 | **CI pipeline.** GitHub Actions running `pytest quant_feature_engine/tests` + `ruff check` + `mypy` on Linux and Windows, on every PR and `main` push. | 1 day | A test failure on either OS blocks the merge; status badge visible. |
| A3 | **Install git on the server + adopt SKILL.md Mode A sync.** Stop `scp`'ing files. | 0.5 day (mostly waiting for `winget`) | `git pull` is the only sync command in any runbook. `scripts/sync_to_server.ps1` removed if it existed. |
| A4 | **One-command install script for both OSes.** `scripts/install_qfe.{sh,ps1}` that creates / refreshes the `.venv` and pins via lockfile. | 0.5 day | A clean checkout reaches "tests green" with one command. |

### Priority B — must close before relying on outputs

| # | Deliverable | Effort | Acceptance criterion |
|---|---|---|---|
| B1 | **Baseline-diff mode in `validate_qfe_real_data.py`.** `--baseline-dir <path>`; on success, write features there; on subsequent runs, diff against that baseline and report row-level deltas. | 1 day | Re-running the sweep with `--baseline-dir` exits 0 with "no drift"; tampering with a baseline file produces a clear, line-numbered diff. |
| B2 | **SHA256 column in `Manifest`.** Append the SHA256 of each written Parquet file when registering it. | 0.5 day | Manifest schema gains `file_sha256` column; existing tests updated to assert presence; corrupted file detected by `Manifest.verify()`. |
| B3 | **Real-data fixture in the test suite.** Tiny (~1 MB), committed to the repo, used by a CI test that runs the full backfill+streaming parity end-to-end on the *same* data on every PR. | 1 day | New test `test_real_fixture_parity` exists, runs on Linux + Windows in CI, completes in < 5s. |
| B4 | **Multi-day catalog ingestion (one instrument family).** Ingest at least 10 trading days for IF*, IH*, or IC*. Re-run the chunk stress + add cross-day chunk sizes (e.g. 500, 5000). | 1–3 days (depends on data source) | Real-data sweep passes for both `cross_day="continuous"` and `cross_day="reset"` features over ≥10 distinct dates. |
| B5 | **EOD archiver real-data validation.** Round-trip: streaming → archive → re-read → assert equal to offline backfill from raw. | 1 day | A new test (or harness) executes this round-trip on real data and exits 0. |

### Priority C — needed once the framework runs in a live process

| # | Deliverable | Effort | Acceptance criterion |
|---|---|---|---|
| C1 | **Prometheus metrics.** `StreamingEngine` exposes `batches_total`, `rows_total`, `errors_total`, `checkpoint_lag_seconds`, `last_batch_ts`. | 1 day | A scrape from the running engine returns the four series; one alert rule shipped. |
| C2 | **Halt-on-consecutive-errors policy.** New config knob `halt_after_n_errors`; engine stops cleanly and writes a final checkpoint instead of looping with nulls. | 0.5 day | Force-injected exception during streaming halts after N batches; checkpoint exists. |
| C3 | **`docs/RUNBOOK.md`.** One section per failure mode in §7.5: streaming error, EOD failure, Redis unreachable, manifest corruption, version-bump rollback. | 1 day | A reader unfamiliar with the framework can recover from each failure mode using only the runbook. |
| C4 | **Structured logging.** Switch to `structlog` or JSON formatter with stable field names (`feature`, `partition`, `batch_id`, `latency_ms`). | 0.5 day | Logs parseable as JSON; one example dashboard query documented. |
| C5 | **Health check + checkpoint freshness assertion on startup.** Engine refuses to start if its checkpoint is older than `max_checkpoint_age_seconds` without an explicit `--accept-stale` flag. | 0.5 day | Mocked stale checkpoint causes `SystemExit(2)` with a clear message. |

### Priority D — defer until proven necessary

| # | Deliverable | Effort | Acceptance criterion |
|---|---|---|---|
| D1 | **Real trade-volume integration.** Extend `TickToBarAggregator` with `volume_mode="trade_size"` once a trade-tick feed exists. | 1 day after data lands | Volume-based features compared against a known-good external source. |
| D2 | **Multi-symbol real-data streaming.** Build a multi-symbol Parquet partition, run streaming engine, assert per-symbol parity. | 1 day | Real-data multi-symbol test passes. |
| D3 | **Ray distributed offline.** Only when single-machine backfill is the bottleneck. Current corpus is 4.9 MB total. | 1–2 days | A multi-machine run agrees with a single-machine run on the same partitions. |
| D4 | **Live message-bus adapter** (Nautilus bus / Kafka / ZMQ). Replace `ReplayAdapter`. | 2–3 days | Side-by-side comparison of a live-stream-then-archive cycle against an offline backfill from the same window agrees within `1e-6`. |

---

## 9. Provenance

Validation runs executed on `D:\nautilus` (host `172.16.112.81`) via the SSH
setup described in [REMOTE_ACCESS.md](REMOTE_ACCESS.md) *(to be written — see
A3)*. Outputs in §4 are verbatim from those sessions with PowerShell
encoding noise removed. CSV inventory and raw bars live under
`D:\nautilus\outputs\qfe_catalog_inventory\` and `D:\nautilus\data\raw\`
respectively.

For framework architecture, see [INTEGRATION.md](../INTEGRATION.md).
