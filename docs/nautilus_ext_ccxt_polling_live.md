# nautilus\_ext ccxt Polling Paper Live

Production engineering guide for the ccxt REST-based polling paper live runner.

This module drives a pure-Python signal engine (e.g. `VolumeWeightedMomentumShortSignalEngine`)
with real-time OHLCV bars fetched via ccxt REST polling.
It is **not** a Nautilus `TradingNode` / `LiveDataEngine` — it is a lightweight
standalone loop intended for paper live validation of strategy logic before
connecting to a full live trading infrastructure.

---

## 1. What this IS and IS NOT

| Feature | This module | Nautilus TradingNode |
|---|---|---|
| Data source | ccxt REST polling | Nautilus LiveDataClient / WebSocket |
| Bar delivery | Python loop + `time.sleep()` | Event-driven engine |
| Signal engine | Direct Python call | `Strategy.on_bar()` lifecycle |
| Order submission | **Never (dry_run always)** | Real broker connection |
| Portfolio tracking | Manual position counter | Full Nautilus Portfolio |
| Performance | Adequate for paper validation | Production-grade |

**Security invariant**: `enable_order_submit` always raises `NotImplementedError`.
No real orders are submitted to any exchange under any circumstances.

---

## 2. Installation

```bash
pip install ccxt pyarrow pandas
```

`nautilus_ext.ccxt_live` works without compiled nautilus_trader Cython
extensions — all 27 tests pass on a plain Python environment.
The Nautilus Instrument is only needed inside `CcxtPollingBarFeed.initialize()`;
all other modules are Nautilus-free.

---

## 3. How polling differs from historical backtest

| Aspect | Historical (CcxtBarDataConnector) | Polling (CcxtPaperLiveRunner) |
|---|---|---|
| Date range | fixed `since` → `until` | open-ended from `since` |
| Data fetch | one-shot paginated download | loop: fetch last N bars every T seconds |
| Deduplication | within one download batch | across polls via seen-timestamp set |
| Signal engine driver | Nautilus BacktestEngine | direct `signal_engine.update(BarInput)` |
| Output | Nautilus `list[Bar]` | per-bar CSV/Parquet records |

---

## 4. Configuration reference (`CcxtPollingLiveConfig`)

| Field | Type | Default | Description |
|---|---|---|---|
| `exchange_id` | str | **required** | ccxt exchange id: `"binance"`, `"okx"`, `"bybit"` |
| `market_type` | str | **required** | `"spot"` / `"swap"` / `"future"` |
| `symbol` | str | **required** | Single ccxt symbol, e.g. `"BTC/USDT:USDT"` |
| `timeframe` | str | **required** | `"1m"`, `"5m"`, `"1h"`, `"1d"`, … |
| `venue` | str | **required** | Nautilus Venue name, e.g. `"BINANCE"` |
| `poll_interval_seconds` | float | `60.0` | Seconds between REST polls |
| `lookback_bars` | int | `5` | Candles to fetch per poll (≥ 2) |
| `drop_incomplete_bar` | bool | `True` | Drop the still-open last bar of each fetch |
| `output_dir` | str\|None | `None` | Directory for CSV/Parquet/JSON outputs |
| `dry_run` | bool | `True` | Always True; cannot be disabled |
| `enable_order_submit` | bool | `False` | **Must be False**; raises `NotImplementedError` if True |
| `max_runtime_seconds` | float\|None | `None` | Stop after N seconds |
| `max_bars` | int\|None | `None` | Stop after N new bars |
| `since` | str\|None | `None` | ISO-8601 UTC warmup start. If None, uses `warmup_bars` |
| `warmup_bars` | int | `100` | Bars to download for indicator warm-up |
| `api_key/secret/password` | str\|None | `None` | Optional; read from env vars if None |
| `params` | dict\|None | `None` | Extra kwargs forwarded to ccxt exchange constructor |
| `price_type` | str | `"LAST"` | Nautilus PriceType for BarType string |
| `source` | str | `"EXTERNAL"` | Nautilus AggregationSource for BarType string |
| `instrument_kind` | str\|None | `None` | Force `"spot"`, `"perpetual"`, or `"future"` |
| `trade_size` | float | `1.0` | Notional quantity for dry-run order intent records |

**Credentials**: Public OHLCV data never needs an API key.
Never store secrets in code or committed config files.

---

## 5. How to configure for each exchange

### Binance (swap/perpetual)

```python
CcxtPollingLiveConfig(
    exchange_id="binance",
    market_type="swap",
    symbol="BTC/USDT",            # Binance perpetual notation
    timeframe="1m",
    venue="BINANCE",
    poll_interval_seconds=60,
    warmup_bars=200,
)
```

### OKX (perpetual)

```python
CcxtPollingLiveConfig(
    exchange_id="okx",
    market_type="swap",
    symbol="BTC/USDT:USDT",       # OKX uses settle notation
    timeframe="1m",
    venue="OKX",
    poll_interval_seconds=60,
)
```

### Bybit (perpetual)

```python
CcxtPollingLiveConfig(
    exchange_id="bybit",
    market_type="swap",
    symbol="BTC/USDT:USDT",
    timeframe="1m",
    venue="BYBIT",
    poll_interval_seconds=60,
)
```

---

## 6. Quick start

```python
from nautilus_ext.ccxt_live import CcxtPollingLiveConfig, CcxtPaperLiveRunner
from nautilus_ext.strategies.vwm_short_signals import (
    VolumeWeightedMomentumShortSignalEngine,
)
from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig

config = CcxtPollingLiveConfig(
    exchange_id="binance",
    market_type="swap",
    symbol="BTC/USDT",
    timeframe="1m",
    venue="BINANCE",
    poll_interval_seconds=60,
    lookback_bars=5,
    warmup_bars=200,
    output_dir="outputs/ccxt_live/BTCUSDT_1m",
    dry_run=True,
)

signal_engine = VolumeWeightedMomentumShortSignalEngine(
    VwmShortSignalConfig(mom_len=5, avg_len=20, atr_len=5)
)

runner = CcxtPaperLiveRunner(config, signal_engine)
summary = runner.run(max_bars=100)   # stop after 100 live bars
print(summary)
```

---

## 7. Output files

When `output_dir` is set, the runner writes these files on stop:

```
output_dir/
    received_bars.csv         every new bar received during the live session
    received_bars.parquet     same, Parquet format
    signals.csv               per-bar signal engine output (all fields)
    signals.parquet           same, Parquet format (only when signals > 0)
    orders.csv                dry-run order intents (entry/exit, always written)
    run_info.json             session metadata summary
```

### `signals.csv` columns

| Column | Description |
|---|---|
| `ts_event` | Millisecond POSIX timestamp |
| `datetime` | ISO-8601 UTC string |
| `instrument_id` | Nautilus InstrumentId string |
| `bar_type` | Nautilus BarType string |
| `open/high/low/close/volume` | OHLCV floats |
| `current_bar` | Bar counter in the signal engine |
| `momentum` | Raw momentum value |
| `vwm` | Volume-weighted momentum EMA |
| `atr` | ATR value |
| `bull_setup / bear_setup` | Setup detection booleans |
| `se_price` | Setup-entry reference close price |
| `s_setup` | Bars since last bear setup |
| `entry_signal / exit_signal` | True when triggered |
| `entry_setup_active` | True when setup conditions met |
| `entry_trigger_price` | Short entry stop trigger level |
| `reason` | `"enter_short"` \| `"exit_short"` \| None |
| `position` | Position after this bar: -1 / 0 / 1 |

### `orders.csv` columns

| Column | Description |
|---|---|
| `ts_event` | Bar timestamp (ms) |
| `datetime` | ISO-8601 UTC string |
| `instrument_id` | Nautilus InstrumentId string |
| `side` | `"SELL"` (entry short) \| `"BUY"` (exit short) |
| `order_type` | `"stop_market"` \| `"market"` |
| `trigger_price` | Entry stop level (float \| None) |
| `quantity` | `config.trade_size` (notional) |
| `reason` | Signal reason string |
| `status` | Always `"dry_run_intent"` |

### `run_info.json` (example)

```json
{
  "exchange_id": "binance",
  "symbol": "BTC/USDT",
  "market_type": "swap",
  "timeframe": "1m",
  "venue": "BINANCE",
  "instrument_id": "BTCUSDT-PERP.BINANCE",
  "bar_type": "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
  "warmup_bars": 200,
  "poll_interval_seconds": 60.0,
  "dry_run": true,
  "enable_order_submit": false,
  "total_bars_received": 100,
  "total_signals": 3,
  "total_order_intents": 2,
  "elapsed_seconds": 6011.4,
  "utc_end": "2024-01-02T01:40:11+00:00"
}
```

---

## 8. Verifying the strategy receives new bars

Add logging to confirm bars flow through:

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

runner = CcxtPaperLiveRunner(config, signal_engine)
runner.run(max_bars=5)
```

Expected log output on each poll:
```
2024-01-01 00:01:10  nautilus_ext.ccxt_live.polling_bar_feed  INFO
    poll_once: fetched 6 rows → 1 new bars  last=2024-01-01 00:01:00+00:00  (0.31s)
```

You can also watch the `signals.csv` grow in real time:
```bash
watch -n 60 "wc -l outputs/ccxt_live/BTCUSDT_1m/signals.csv"
```

---

## 9. Running tests

```bash
# All 27 polling live tests (no Nautilus required)
python -m pytest nautilus_ext/tests/test_ccxt_polling_live.py -v

# Full suite including historical connector
python -m pytest nautilus_ext/tests/ -v
# → 35 passed, 10 skipped (Cython not compiled locally)
```

---

## 10. Current limitations

1. **Single symbol only.** Each runner handles one symbol.
   For multi-symbol live, create one runner per symbol.

2. **REST polling latency.** Bar delivery is delayed by `poll_interval_seconds`
   (typically 60 s) plus network round-trip.  For sub-second delivery use
   ccxt.pro WebSocket — see §11.

3. **No Nautilus Portfolio.** Position tracking is a manual integer counter
   (`-1 / 0 / 1`).  P&L, margin, and fill simulation are not implemented.

4. **No reconnection logic.** `fetch_ohlcv` retries 3 times with exponential
   back-off; after that the exception propagates and the runner stops.

5. **Not a full TradingNode.** `BaseBarStrategy.on_bar()` (which calls
   `order_factory`, `portfolio`, `cache`) cannot be invoked from this runner
   without a Nautilus `TradingNode` context.

---

## 11. Upgrade path to Nautilus TradingNode

When ready for a full live trading setup:

```
CcxtPaperLiveRunner  →  Nautilus LiveDataClient (ccxt adapter)
                              ↓
                         TradingNode.run()
                              ↓
                         BaseBarStrategy.on_bar()
                              ↓
                         order_factory.stop_market() / portfolio checks
```

Steps:
1. Implement `CcxtLiveDataClient(LiveDataClient)` wrapping `CcxtPollingBarFeed`
   or `ccxt.pro` WebSocket.
2. Register with `TradingNode` via `LiveDataClientConfig`.
3. Plug in `BaseBarStrategy` (or your subclass) as the strategy.
4. The signal engine, instrument mapper, and bar mapper in
   `nautilus_ext.ccxt_live` remain usable as-is.

---

## 12. Upgrade path to ccxt.pro WebSocket

`ccxt.pro` provides async WebSocket for real-time bar streaming:

```python
# Sketch — not yet implemented
import asyncio
import ccxt.pro as ccxtpro

async def stream_bars(symbol: str, timeframe: str):
    exchange = ccxtpro.binance()
    while True:
        ohlcvs = await exchange.watch_ohlcv(symbol, timeframe)
        for ohlcv in ohlcvs:
            bar_input = BarInput(open=ohlcv[1], high=ohlcv[2],
                                  low=ohlcv[3], close=ohlcv[4], volume=ohlcv[5])
            result = signal_engine.update(bar_input, ...)
            ...
```

The `CcxtInstrumentMapper` and `CcxtBarMapper` classes in `nautilus_ext.ccxt`
are reusable in both sync-REST and async-WebSocket contexts.

---

## 13. Security constraints

- `enable_order_submit=True` always raises `NotImplementedError`.
- `dry_run=True` is the only supported mode.
- No API key is needed for public OHLCV data.
- Credentials (`api_key`, `secret`, `password`) are read from environment
  variables and are **never** logged or printed.
- No existing data in `output_dir` is deleted; files are written fresh each run.
- No real orders are submitted under any code path.

---

## Module structure

```
nautilus_ext/ccxt_live/
    __init__.py              Public API (lazy exports)
    polling_config.py        CcxtPollingLiveConfig dataclass
    polling_bar_feed.py      CcxtPollingBarFeed: warmup + poll
    paper_live_runner.py     CcxtPaperLiveRunner: main loop
    signal_recorder.py       SignalRecorder: per-bar CSV/Parquet log
    dry_run_execution.py     DryRunExecutionRecorder: order intent log

nautilus_ext/tests/
    test_ccxt_polling_live.py   27 unit tests (no real network, no Nautilus required)

docs/
    nautilus_ext_ccxt_polling_live.md   This document
```
