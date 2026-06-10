# data_engine — data processing layer

The formal, standalone data layer. **Our own design — it does not depend on
Nautilus Trader's native data system** (Nautilus integration is out of scope).

Importable directly:

```python
from data_engine import BarEvent, load_events, make_bar_event, make_bars
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `events.py` | `BarEvent` dataclass (OHLCV + `instrument_id` + `event_time_ns` + `event_type`) |
| `time.py` | `ONE_SECOND_NS`, `to_event_time_ns(value, unit)` for ns/us/ms/s |
| `validation.py` | `require_numeric`, `optional_numeric` (no pandas) |
| `schema.py` | bar field-name constants |
| `split.py` | `split_warmup_live(events, warmup_bars)` |
| `adapters/bar_adapter.py` | `make_bar_event(...)`, `make_bars(...)` |
| `streams/base.py` | `EventSource` protocol (`warmup()` / `stream()`) |
| `sources/synthetic.py` | `SyntheticBarSource` + `load_synthetic_bars` |
| `sources/csv_bars.py` | `CsvBarSource` + `load_csv_bars` (stdlib `csv`) |
| `sources/parquet_bars.py` | `ParquetBarSource` + `load_parquet_bars` (Hive Parquet via `pyarrow`) |
| `sources/live_synthetic.py` | `LiveSyntheticBarSource` + `load_live_synthetic` (generator) |
| `loader.py` | `load_events(data_config)` — canonical entry, dispatches by `data.mode` |

## Data modes (`data.mode` in a strategy config)

| Mode | Source | Live events |
|------|--------|-------------|
| `synthetic` | generated flat→rise→fall demo path | list |
| `csv_bars` | historical replay from a local CSV (small demos/tests) | list |
| `parquet_bars` / `hive_parquet_bars` | historical replay from a Hive-partitioned Parquet dataset (production-style) | list |
| `live_synthetic` | streaming skeleton (no real feed) | generator |

All historical sources sort by event time **once** after loading, then split the
first `warmup_bars` rows as warmup. Missing O/H/L default to `close`, missing
volume to `0.0`, and a missing timestamp column yields monotonic 1-second
timestamps.

- **`csv_bars`** — for small demo/test files (stdlib `csv`, no extra deps).
- **`parquet_bars` / `hive_parquet_bars`** — preferred for larger historical
  backtests. Reads a Hive-partitioned dataset with `pyarrow.dataset` using
  **partition pruning** (simple equality `filters`) and **column pushdown**
  (only needed bar columns are read). Example config:

  ```yaml
  data:
    mode: parquet_bars        # or: hive_parquet_bars
    root: data/bars
    instrument_id: BTC/USDT
    warmup_bars: 20
    partition_cols: [trading_date, instrument_id]
    filters:
      trading_date: "2024-01-01"
      instrument_id: BTC/USDT
    timestamp_column: event_time_ns
    timestamp_unit: ns
  ```

  `pyarrow` is the only added dependency; **no pandas**. The source returns plain
  `BarEvent` objects and shares the design (not the code) of the older
  `quant_feature_engine` Parquet store.

## Adding a real live source later

Implement `data_engine.streams.base.EventSource` (a `warmup()` + a
`stream()`), add a `load_<mode>` and register it in `loader.py`'s dispatch, then
reference the new `data.mode` from a config. No network/exchange dependency is
present today.

## Layering

`data_engine` (data) → `feature_engine/compute` (features) →
`strategy_framework` (orchestration) → `strategies/<name>` (logic).
`strategy_framework/data_loaders.py` is a **compatibility wrapper** that
re-exports this package; it is not the canonical implementation.
