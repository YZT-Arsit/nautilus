# feature_engine OHLCV feature library

Pure-Python, incrementally-updated technical features built on the existing
`FeatureSpec → BackendRegistry → PythonBackend` design. Every feature here:

- is a `FeatureBase` subclass in `feature_engine/compute/features.py`,
- updates in O(1) / amortized O(1) per event (no full-history recompute),
- reports `not_ready` until it has enough history,
- guards divide-by-zero with `eps = 1e-12` (`max(denom, eps)`),
- handles missing fields (single-field features flag `skipped_missing_field`;
  multi-field bar features return the cached not-ready value),
- returns a `FeatureValue` with the standard
  `source_event_time_ns` / `update_status` semantics.

**Independence:** `feature_engine` remains independent from Nautilus. None of
these features import `nautilus_trader` or use a Nautilus indicator — the maths
references standard TA definitions but is implemented in plain Python. They are
offline-capable, unit-testable, reusable, and work with any (non-Nautilus)
backend.

## Usage

```python
from feature_engine.api import atr_spec, zscore_spec
from feature_engine.compute.backend import build_default_registry

registry = build_default_registry()              # python backend
atr = registry.create_feature(atr_spec("atr_14", window=14))
for bar in bars:
    update = atr.update(bar)
    if update.value.is_ready:
        print(update.value.value)
```

Each feature dispatches by `params["type"]` (set by its builder), so resolution
is explicit and never depends on the feature name.

## Feature reference

`eps = 1e-12`. `n` = `window`. `close[-n]` = close `n` bars ago.

### A. Price / bar structure

| type | builder | formula |
| --- | --- | --- |
| `rolling_range` | `rolling_range_spec` | `high - low` |
| `true_range` | `true_range_spec` | `max(high-low, |high-prev_close|, |low-prev_close|)` (first bar: `high-low`) |
| `candle_body_ratio` | `candle_body_ratio_spec` | `|close - open| / max(high - low, eps)` |
| `upper_shadow_ratio` | `upper_shadow_ratio_spec` | `(high - max(open, close)) / max(high - low, eps)` |
| `lower_shadow_ratio` | `lower_shadow_ratio_spec` | `(min(open, close) - low) / max(high - low, eps)` |

### B. Trend / momentum

| type | builder | formula |
| --- | --- | --- |
| `return_n` | `return_n_spec` | `close / close[-n] - 1` |
| `momentum_n` | `momentum_n_spec` | `close - close[-n]` |
| `price_position` | `price_position_spec` | `(close - min(low, n)) / max(max(high, n) - min(low, n), eps)` |
| `drawdown_from_rolling_high` | `drawdown_from_rolling_high_spec` | `close / max(close, n) - 1` |
| `breakout_up` | `breakout_up_spec` | `close > previous rolling_max(high, n)` (bool) |
| `breakout_down` | `breakout_down_spec` | `close < previous rolling_min(low, n)` (bool) |

`breakout_*` evaluate against the rolling extreme of the **prior** `n` bars
(the current bar's high/low is added only after the comparison), so a bar cannot
break out against itself.

### C. Volatility

| type | builder | formula |
| --- | --- | --- |
| `atr` | `atr_spec` | rolling mean of `true_range` over `n` (simple MA, not Wilder) |
| `volatility_ratio` | `volatility_ratio_spec` | `realized_vol(short) / max(realized_vol(long), eps)`; realized vol = sample std of log close-to-close returns |
| `bollinger_width` | `bollinger_width_spec` | `(upper - lower) / max(middle, eps)`, with `middle = mean(close, n)`, `upper/lower = middle ± k·std(close, n)` |
| `bollinger_percent_b` | `bollinger_percent_b_spec` | `(close - lower) / max(upper - lower, eps)` |

`volatility_ratio` takes `short_window` / `long_window`; the Bollinger features
take `k` (default 2.0).

### D. Normalization / volume

| type | builder | formula |
| --- | --- | --- |
| `zscore` | `zscore_spec` | `(x - mean(x, n)) / max(std(x, n), eps)` (`x` = `input_field`, default `close`) |
| `volume_zscore` | `volume_zscore_spec` | `zscore(volume, n)` |
| `volume_ratio` | `volume_ratio_spec` | `volume / max(mean(volume, n), eps)` |
| `quote_volume` | `quote_volume_spec` | event `quote_volume` if present, else `close * volume` |
| `vwap_distance` | `vwap_distance_spec` | `close / max(vwap, eps) - 1` (session VWAP, or a rolling count/time window) |

## Tests

`nautilus_ext/tests/test_feature_library_ohlcv.py` covers, per feature:
warmup/not_ready, exact ready output on a known bar series, divide-by-zero
guards, and missing-field handling — plus that `PythonBackend.available_feature_types()`
lists every new type, that every builder spec is buildable by `BackendRegistry`,
a `state_dict` round-trip, and that the compute modules contain no
`nautilus_trader` import.
