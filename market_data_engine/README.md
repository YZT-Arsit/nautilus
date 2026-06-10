# market_data_engine — data processing layer

The formal, standalone data layer. **Our own design — it does not depend on
Nautilus Trader's native data system** (Nautilus integration is out of scope).

Importable directly:

```python
from market_data_engine import BarEvent, load_events, make_bar_event, make_bars
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
| `sources/live_synthetic.py` | `LiveSyntheticBarSource` + `load_live_synthetic` (generator) |
| `loader.py` | `load_events(data_config)` — canonical entry, dispatches by `data.mode` |

## Data modes (`data.mode` in a strategy config)

| Mode | Source | Live events |
|------|--------|-------------|
| `synthetic` | generated flat→rise→fall demo path | list |
| `csv_bars` | historical replay from a local CSV | list |
| `live_synthetic` | streaming skeleton (no real feed) | generator |

`csv_bars` reads one series, sorts by event time **once** in the loader, then
splits the first `warmup_bars` rows as warmup. Missing O/H/L default to `close`,
missing volume to `0.0`, and a missing timestamp column yields monotonic
1-second timestamps.

## Adding a real live source later

Implement `market_data_engine.streams.base.EventSource` (a `warmup()` + a
`stream()`), add a `load_<mode>` and register it in `loader.py`'s dispatch, then
reference the new `data.mode` from a config. No network/exchange dependency is
present today.

## Layering

`market_data_engine` (data) → `nautilus_ext/features/compute` (features) →
`strategy_framework` (orchestration) → `strategies/<name>` (logic).
`strategy_framework/data_loaders.py` is a **compatibility wrapper** that
re-exports this package; it is not the canonical implementation.
