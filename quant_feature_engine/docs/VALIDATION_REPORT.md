# quant_feature_engine — Validation Report

**Status:** ✅ MVP validated for engineering correctness on Windows + real CFFEX
catalog data.

This document is the canonical evidence trail for "does the framework do what it
claims to do?" It captures both synthetic and real-data validation runs, the
exact environment they were executed in, and the limitations that scope what
"validated" means in this context.

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

## 3. Engineering validation vs performance validation

This is the central distinction. **The runs in this report establish
engineering correctness; they do not establish trading performance.**

| What we validated | What we did **not** validate |
|---|---|
| Offline backfill and streaming replay produce identical features (within `1e-6`) on real catalog data. | Whether those features are profitable, predictive, or behave like the strategy expects in live trading. |
| Per-symbol state isolation in `PerSymbolMixin` — features for symbol A do not leak into symbol B. | The economic meaningfulness of any feature value computed from `synthetic_tick_count` volume. |
| Schema stability across micro-batch boundaries, including chunks aligned exactly to feature window lengths. | Latency under live ingestion load (tick rates, jitter). |
| Hive-Parquet round-trip via `ParquetStore` — read filters prune to a single partition; partition columns don't pollute the result schema. | Behaviour at file-system limits, network filesystems, or cloud object stores. |
| Manifest dedup so backfill is idempotent. | Concurrent-writer safety on the manifest. |
| EOD archiver stage-then-commit semantics on idempotent re-runs. | EOD archiver under crash injection or partial writes across multi-day data. |

The single line that summarises the position: **the framework moves data and
computes features faithfully; whether those features matter is a separate
question the framework does not try to answer.**

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

## 7. Limitations (scope of what "validated" means)

These limit how far the current evidence extends. None of them indicates a bug;
each is a deliberately bounded scope.

1. **Single trading date in the verified catalog.** All 16 CFFEX instruments
   in `D:\QuanHub\DataHome\DataTrans\nautilus_catalog` cover only
   `2023-01-03 01:29–07:00 UTC`. Cross-day rolling-window behaviour, the
   `cross_day="reset"` policy, and EOD archive across days are exercised
   only by the synthetic 2-date unit test
   (`test_streaming_matches_offline_end_to_end`). True multi-day real-data
   validation is blocked on catalog growth, not framework state.

2. **Synthetic tick-count volume.** The catalog stores L1 quote ticks, not
   trade ticks. `TickToBarAggregator` uses
   `volume_mode="tick_count"`, which counts the number of quotes per minute.
   Every feature whose semantics depend on real share/contract volume —
   `vwm_20`, `vwm_zscore_60`, any future VWAP/OBV — is computed against
   this synthetic volume and is therefore **engineering-valid but not
   performance-valid** per SKILL.md.

3. **Session-start artifact.** Catalog files begin at `01:29:00 UTC`,
   roughly 30 seconds before the CFFEX day session opens at `01:30:00`. The
   aggregator produces a `01:29:00 → 01:30:00` minute bar containing a
   handful of pre-open quotes (`volume = 1` is typical). Features warm-up
   absorbs it without error, but downstream consumers that compare against
   official session statistics should filter this first bar.

4. **EOD archiver not exercised against real data.** The
   stage-then-commit, manifest-after-data, and `mode={"error","append",
   "overwrite"}` semantics are covered by
   `test_eod_archiver_writes_and_is_idempotent` using synthetic data only.
   The archiver has not yet been driven against real-data partitions on
   Windows under multi-day load.

5. **No distributed (Ray) backend exercised.** `RayBatchEngine` exists in
   `quant_feature_engine/execution/distributed.py` and mirrors the
   single-machine `BatchEngine.run` contract, but no multi-node test has
   been run. Single-machine offline (via `concurrent.futures.ProcessPool`
   or `n_workers=1`) is fully covered.

6. **No live message-bus integration.** Streaming was driven by
   `ReplayAdapter` (Parquet → micro-batches at machine speed). Wiring into
   Nautilus's actual message bus, ZMQ, or Kafka is per
   `INTEGRATION.md` but is not part of this evidence.

7. **Tolerance is `1e-6`, not exact.** Observed differences are usually
   bit-identical in this run, but `1e-6` is the contract. Tightening to
   `0.0` would require a guarantee that no future polars / pyarrow upgrade
   introduces benign FMA reordering.

---

## 8. Next milestones

In rough priority order, each unblocks one of the limitations above.

1. **Catalog multi-day ingestion.** Add at least a week of CFFEX QuoteTicks
   for one instrument family (e.g. IF*). Then re-run the chunk-stress sweep
   with chunks that span day boundaries (e.g. `chunk_sizes = 60, 1000,
   5000`) to confirm the `cross_day="continuous"` default and the
   `cross_day="reset"` policy both behave correctly on real data.

2. **EOD archiver real-data validation.** With ≥2 days available, run the
   streaming engine over the first day, archive via `EodArchiver`, then run
   the offline backfill over the result and verify byte-identity to a
   fresh offline backfill from raw. This validates the
   stage-then-commit + manifest semantics end-to-end on Windows.

3. **Real trade-volume integration.** Either ingest a trade-tick feed
   alongside the existing quote-tick catalog, or extend
   `TickToBarAggregator` with a `volume_mode="trade_size"` once trade
   ticks are available. Volume-based features (`vwm_*`) graduate from
   engineering-valid to performance-valid.

4. **Multi-symbol real-data streaming stress.** Build raw bars for several
   CFFEX instruments and run a single streaming engine across an
   interleaved stream. Exercises the per-symbol state isolation path on
   real data (currently only synthetic).

5. **Ray distributed offline backfill.** Once items 1–3 are stable, fan
   `BatchEngine` out across multiple instruments via `RayBatchEngine`. Not
   urgent — the entire CFFEX corpus is 4.9 MB / 268K ticks and fits
   comfortably in a single process.

6. **Live message-bus adapter.** Replace `ReplayAdapter` with a Nautilus
   bus subscriber (see `INTEGRATION.md` §3 for the wiring sketch). At this
   point the framework moves from offline-validated to live-validated.

---

## 9. Provenance

This report describes a sequence of validation runs executed in May 2026 on
the remote Windows server defined in `SKILL.md` (`172.16.112.81`, working
directory `D:\nautilus`). All commands above were executed; the outputs were
captured verbatim from the SSH session and reproduced in §4 with line-noise
removed. CSV artifacts and raw bar files live under
`D:\nautilus\outputs\qfe_catalog_inventory\` and `D:\nautilus\data\raw\`
respectively.

For the framework architecture and rationale, see
[INTEGRATION.md](../INTEGRATION.md).
