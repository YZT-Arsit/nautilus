# Integrating `quant_feature_engine` with existing `nautilus_ext` modules

This document explains how the new framework relates to existing modules in
`nautilus_ext/` and which call-sites should be updated. The guiding rule:
**do not duplicate**. Where `nautilus_ext` already does something well, the
new framework calls into it; where the new framework adds a missing capability,
existing modules call into it.

---

## Module map

| Existing module | Status | What changes |
|---|---|---|
| [`nautilus_ext/features/base.py`](../nautilus_ext/features/base.py) | **Keep as-is** | Defines the `BarFeatureEngine` protocol used inside Nautilus strategies. The new framework's `Feature` class is a **superset**, but the protocol is the public contract that `BaseBarStrategy` depends on. Don't touch it. |
| [`nautilus_ext/features/vwm_features.py`](../nautilus_ext/features/vwm_features.py) | **Keep as-is** | Strategy-facing bar-by-bar engine. Conserves TradeBlazer `[1]` semantics needed by `vwm_short_signals.py`. Different concern from offline panel features. |
| [`nautilus_ext/features/tradeblazer_features.py`](../nautilus_ext/features/tradeblazer_features.py) | **Keep as-is** | Low-level primitives (`cross_over`, `RawMomentumFeature`). Used by `vwm_features.py`. Pure logic, no IO. |
| [`nautilus_ext/features/nautilus_indicators.py`](../nautilus_ext/features/nautilus_indicators.py) | **Keep as-is** | Adapters around Nautilus C-level indicators. |
| [`nautilus_ext/data/catalog_quote_reader.py`](../nautilus_ext/data/catalog_quote_reader.py) | **Wrap, don't replace** | Already reads the verified `nautilus_catalog`. The new framework treats it as an **upstream source**. See "Bar aggregation bridge" below. |
| [`nautilus_ext/data/event_source.py`](../nautilus_ext/data/event_source.py) | **Keep** | Event-iterator abstraction over the catalog. The framework's `CallbackAdapter` can be driven by this. |
| [`nautilus_ext/pipelines/batch_feature_pipeline.py`](../nautilus_ext/pipelines/batch_feature_pipeline.py) | **Bridge to new framework** | Currently runs **per-strategy** engines (`BarFeatureEngine`) over historical events. For *panel-style* features (cross-symbol, multi-day, persisted) call `quant_feature_engine.execution.batch_engine.BatchEngine`. The two coexist: strategy-level (per-bar, in-memory) vs. analytical (panel, Parquet-persisted). |
| [`nautilus_ext/pipelines/stream_feature_pipeline.py`](../nautilus_ext/pipelines/stream_feature_pipeline.py) | **Bridge to new framework** | Equivalent rebridge for streaming. The `state_store` parameter it already accepts maps directly onto `quant_feature_engine.core.state.StateStore`. |
| [`nautilus_ext/pipelines/warmup_pipeline.py`](../nautilus_ext/pipelines/warmup_pipeline.py) | **Keep** | Bar-by-bar warmup for strategy engines. Orthogonal. |

---

## Two layers, not one

The existing `nautilus_ext` features and the new `quant_feature_engine` solve
overlapping but **distinct** problems:

### Layer 1 — Strategy-time bar features (`nautilus_ext/features/`)

* Live inside a single strategy instance.
* Operate on `BarInput` objects.
* Maintain micro-state (e.g. last bar's VWM, last bar's ATR) needed to preserve
  TradeBlazer `[1]` semantics.
* Are not persisted to disk.
* Are not shared across strategies.

This is the **right** representation for live trading inside Nautilus. Don't
change it.

### Layer 2 — Panel features (`quant_feature_engine/`)

* Operate on cross-symbol, multi-day Polars DataFrames.
* Persist to Hive-partitioned Parquet so model training and research can reuse
  them.
* Need dependency resolution (DAG), distributed compute, EOD archive.
* Share code between offline backfill and intraday streaming via a single
  `update(batch)` method.

This is what the existing pipelines were beginning to do but at a smaller
scale; the new framework formalises it.

---

## Concrete integration points

### 1. Bar aggregation bridge (catalog QuoteTick → Parquet bars)

The verified catalog at `D:\QuanHub\DataHome\DataTrans\nautilus_catalog`
contains QuoteTick data, **not** OHLCV bars (per SKILL.md). To use the new
framework you need bars in Hive layout under `data/raw/`.

Recommended pattern — a one-shot script under `internal_examples/`:

```python
# internal_examples/build_qfe_raw_from_catalog.py  (sketch)
from nautilus_ext.data.catalog_quote_reader import CatalogQuoteTickSource
from nautilus_ext.data.event_source import QuoteTickAggregator  # or similar
from quant_feature_engine.storage.parquet_store import ParquetStore
import polars as pl

source = CatalogQuoteTickSource(
    catalog_path=r"D:\QuanHub\DataHome\DataTrans\nautilus_catalog",
    instrument_id="IH2303.CFFEX",
    start="2023-01-01", end="2023-12-31",
)
aggregator = QuoteTickAggregator(frequency="1m")
bars = [aggregator.update(q) for q in source.iter_events()]
bars = [b for b in bars if b is not None]

df = pl.DataFrame([{
    "symbol": b.instrument_id,
    "ts_event": b.ts_event,
    "open": b.open, "high": b.high, "low": b.low, "close": b.close,
    "volume": b.volume,
    "turnover": b.volume * b.close,  # synthetic
} for b in bars])

store = ParquetStore(
    "D:/nautilus/data/raw",
    partition_cols=("asset_class", "exchange", "frequency", "trading_date"),
)
# Group by trading_date and write each partition once
df = df.with_columns(pl.col("ts_event").dt.date().alias("trading_date"))
for date, sub in df.group_by("trading_date"):
    store.write(sub.drop("trading_date"), partition_values={
        "asset_class": "futures",
        "exchange": "CFFEX",
        "frequency": "1m",
        "trading_date": date[0].isoformat(),
    })
```

Synthetic volume warning from SKILL.md applies — only use for engineering
validation, not performance claims.

### 2. `BatchFeaturePipeline` → call `BatchEngine` for panel features

When the pipeline is asked for cross-symbol panel features (rather than a
single strategy's per-bar state), delegate:

```python
# In nautilus_ext/pipelines/batch_feature_pipeline.py — optional addition
def run_panel(
    self,
    *,
    raw_root: str,
    feature_root: str,
    manifest_root: str,
    feature_names: list[str],
    partitions: list[dict[str, str]],
) -> list[dict]:
    """Delegate panel-feature backfill to quant_feature_engine."""
    from quant_feature_engine.execution.batch_engine import BatchEngine
    from quant_feature_engine.storage.metadata import Manifest

    return BatchEngine(
        raw_root=raw_root,
        feature_root=feature_root,
        manifest=Manifest(manifest_root),
    ).run(partitions, feature_names)
```

The existing `run()` method that drives `BarFeatureEngine` stays — it's the
right thing for strategy-time work.

### 3. `StreamFeaturePipeline` → reuse `StateStore`

`StreamFeaturePipeline` already accepts a `state_store` with `.save(key, state)`.
Wrap a `quant_feature_engine` store so both layers share the same Redis
namespace:

```python
# nautilus_ext/pipelines/stream_feature_pipeline.py — wrapper helper
def make_compat_state_store(qfe_store):
    """Adapt a quant_feature_engine StateStore to the .save(key, state) API."""
    import pickle
    class _Adapter:
        def save(self, key, state):
            qfe_store.put(key, pickle.dumps(state))
            return key
    return _Adapter()
```

### 4. New panel features should subclass `Feature`, not `BarFeatureEngine`

Adding a feature to `quant_feature_engine/features/` requires:

1. New file under `quant_feature_engine/features/your_feature.py` with a
   `@register` class.
2. Add the import to `quant_feature_engine/features/__init__.py::load_all`.
3. Reference it by name in `config/example.yaml` or wherever feature sets
   are listed.

Adding a feature for **strategy-time use only** continues to go in
`nautilus_ext/features/` as a `BarFeatureEngine`.

### 5. Reusing existing TradeBlazer primitives

`quant_feature_engine` features can freely import from `nautilus_ext.features.tradeblazer_features` (cross_over, cross_under, etc.). The
primitives are pure-Python, no Nautilus runtime dependency. This avoids
duplicating the cross-detection logic between the two layers.

```python
# In a new quant_feature_engine/features/cross.py
from nautilus_ext.features.tradeblazer_features import cross_over
# ... use cross_over in your update()
```

---

## What NOT to do

* **Do not** rewrite `VwmFeatureEngine` as a `Feature`. It exists to preserve
  `[1]` semantics needed by the live strategy; the new framework's per-bar
  semantics are different (panel-wise, vectorised) and the two would diverge.
* **Do not** route live Nautilus bar callbacks through `StreamingEngine` for
  *strategy execution*. Use it for **analytics/feature-publishing** alongside
  the strategy. The strategy's bar handler still goes through
  `BaseBarStrategy.on_bar` → `BarFeatureEngine.update`.
* **Do not** persist strategy-time features (e.g. `VwmFeatureSnapshot`) to the
  Hive Parquet layout. Those are runtime structs, not panel data.

---

## Sanity checklist before integration

1. `quant_feature_engine/tests` all green (39/39 currently).
2. `nautilus_ext.features` `__init__.py` lazy-loading still works (no eager
   polars import).
3. The bar aggregation bridge produces partition layouts matching
   `RAW_PARTITIONS` in `quant_feature_engine/core/schema.py`.
4. Feature names used by both layers are kept **distinct** (e.g.
   `vwm_short_*` in `nautilus_ext`, `vwm_20`, `vwm_zscore_60` in
   `quant_feature_engine`). Same name, different semantics, no collision.
