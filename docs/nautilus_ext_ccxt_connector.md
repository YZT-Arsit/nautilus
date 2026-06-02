# nautilus\_ext ccxt Connector

Production engineering guide for the ccxt-based bar data connector.
This connector downloads exchange market metadata and OHLCV history via
[ccxt](https://github.com/ccxt/ccxt), converts the data into native
NautilusTrader types (`Instrument`, `BarType`, `Bar`), and plugs directly
into the existing `NautilusBacktestRunner` / `NautilusEngineRunner` pipeline.

---

## 1. Why ccxt?

- Unified Python interface to 100+ exchanges (Binance, OKX, Bybit, …).
- Supports spot, perpetual swap, dated future, option markets.
- Downloads public market metadata and OHLCV history without API keys.
- Easy to switch exchange without rewriting data logic.

This stage covers **offline historical download and backtest only**.
Real-time polling (WebSocket, paper trading, live execution) is a future
extension — see §10.

---

## 2. Installation

```bash
pip install ccxt pyarrow pandas
```

ccxt is **not** a hard dependency of nautilus\_ext.  The package import
succeeds even when ccxt is absent; the `ImportError` with a clear install
message is only raised when the exchange is actually instantiated.

---

## 3. Quick start

```python
from nautilus_ext.ccxt import CcxtBarDataConnector, CcxtDataConfig

config = CcxtDataConfig(
    exchange_id="binance",     # any ccxt exchange id
    market_type="swap",        # "spot" | "swap" | "future"
    symbols=["BTC/USDT:USDT"], # ccxt-style symbol (perpetual)
    timeframe="1m",            # ccxt timeframe string
    since="2024-01-01T00:00:00Z",
    until="2024-01-07T00:00:00Z",
    venue="BINANCE",           # Nautilus Venue name
    output_dir="outputs/ccxt/BTCUSDT_1m",
)

connector = CcxtBarDataConnector(config)
bars       = connector.prepare_data()    # list[Bar]
instrument = connector.instrument        # CryptoPerpetual
bar_type   = connector.get_bar_type()    # BarType
```

---

## 4. Configuration reference (`CcxtDataConfig`)

| Field | Type | Default | Description |
|---|---|---|---|
| `exchange_id` | str | **required** | ccxt exchange id: `"binance"`, `"okx"`, `"bybit"`, … |
| `market_type` | str | **required** | `"spot"` / `"swap"` / `"future"` |
| `symbols` | list[str] | **required** | ccxt symbols, e.g. `["BTC/USDT"]` for spot, `["BTC/USDT:USDT"]` for OKX perp |
| `timeframe` | str | **required** | `"1m"`, `"5m"`, `"1h"`, `"1d"`, `"1w"`, `"1M"`, … |
| `since` | str | **required** | ISO-8601 UTC start, e.g. `"2024-01-01T00:00:00Z"` |
| `until` | str | `None` | ISO-8601 UTC end.  If None, stops at the current bar. |
| `limit` | int | `1000` | Max candles per API call. |
| `enable_rate_limit` | bool | `True` | ccxt throttle; always keep True in production. |
| `sandbox` | bool | `False` | Testnet mode.  Most exchanges do not support it for public data. |
| `api_key` | str | `None` | Loaded from `{EXCHANGE_ID_UPPER}_API_KEY` env var if None. |
| `secret` | str | `None` | Loaded from `{EXCHANGE_ID_UPPER}_SECRET` env var if None. |
| `password` | str | `None` | OKX / some futures exchanges. |
| `params` | dict | `None` | Extra kwargs forwarded to ccxt exchange constructor. |
| `output_dir` | str | `None` | Root directory for saved outputs. |
| `save_raw` | bool | `True` | Save `raw_ohlcv.csv`. |
| `save_parquet` | bool | `True` | Save `raw_ohlcv.parquet` and `normalized_bars.parquet`. |
| `venue` | str | `""` | Nautilus Venue name.  Defaults to `exchange_id.upper()`. |
| `base_currency` | str | `None` | Override base currency. |
| `quote_currency` | str | `None` | Override quote currency. |
| `instrument_kind` | str | `None` | Force `"spot"`, `"perpetual"`, `"future"` regardless of ccxt flags. |
| `drop_incomplete_bar` | bool | `True` | Drop the last (still-open) bar. |
| `price_type` | str | `"LAST"` | Nautilus `PriceType` for `BarType`. |
| `source` | str | `"EXTERNAL"` | Nautilus `AggregationSource` for `BarType`. |

**Credentials:**  Only private endpoints need an API key.  Public market
data (OHLCV, market metadata) works without any credential on all major
exchanges.  Never store secrets in code or config files committed to Git.
Use environment variables.

---

## 5. Downloading contract (market) information

```python
connector = CcxtBarDataConnector(config)

# Load all markets and filter to configured symbols
markets = connector.load_markets()        # dict keyed by ccxt symbol

# Per-symbol summary
info = connector.discover()
# {
#   "BTC/USDT:USDT": {
#       "market_type": "swap_linear",
#       "base": "BTC",
#       "quote": "USDT",
#       "settle": "USDT",
#       "active": True,
#   }
# }
```

The raw ccxt market dict is saved to `outputs/ccxt/markets.json` when
`connector.save_outputs()` is called.

---

## 6. Downloading OHLCV history

```python
# Full pipeline
bars = connector.prepare_data()

# Or step by step:
connector.load_markets()
connector.build_instrument("BTC/USDT:USDT")   # builds CryptoPerpetual
ohlcv_df = connector.download_ohlcv("BTC/USDT:USDT")
# ohlcv_df columns: timestamp_ms, open, high, low, close, volume,
#                   datetime (UTC), symbol, exchange, timeframe
```

Pagination is automatic: the connector paginates forward from `since`
in batches of `limit` rows until `until` is reached or the exchange
returns fewer rows than requested.

---

## 7. Instrument mapping (ccxt → Nautilus)

| ccxt market flags | Nautilus type | Instrument ID example |
|---|---|---|
| `spot=True` | `CurrencyPair` | `BTCUSDT.BINANCE` |
| `swap=True, linear=True` | `CryptoPerpetual` | `BTCUSDT-PERP.BINANCE` |
| `swap=True, inverse=True` | `CryptoPerpetual` | `BTCUSD-PERP.BITMEX` |
| `future=True, linear=True` | `CryptoFuture` | `BTCUSDT-20241227.BINANCE` |
| `future=True, inverse=True` | `CryptoFuture` | `BTCUSD-20241227.BITMEX` |

Precision is derived from `market['precision']` (ccxt normalises
DECIMAL\_PLACES and TICK\_SIZE modes automatically).

If the exchange does not provide enough metadata to build a valid Nautilus
Instrument, `NautilusInstrumentFactory.build()` raises a `NotImplementedError`
listing the exact missing fields.  Pass an `InstrumentProfile` override or
set the corresponding config fields (`base_currency`, `quote_currency`, etc.)
to supply the missing data.

---

## 8. Running a backtest

```python
from nautilus_ext.ccxt import CcxtBarDataConnector, CcxtDataConfig
from nautilus_ext.runners.backtest_runner import NautilusBacktestRunner
from nautilus_ext.runners.engine_runner import EngineRunConfig
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.objects import Money, Currency

config = CcxtDataConfig(
    exchange_id="binance",
    market_type="swap",
    symbols=["BTC/USDT:USDT"],
    timeframe="1h",
    since="2024-01-01T00:00:00Z",
    until="2024-03-01T00:00:00Z",
    venue="BINANCE",
)
connector = CcxtBarDataConnector(config)

engine_config = EngineRunConfig(
    venue=Venue("BINANCE"),
    oms_type=OmsType.NETTING,
    account_type=AccountType.MARGIN,
    starting_balances=[Money(100_000, Currency.from_str("USDT"))],
)
runner = NautilusBacktestRunner(connector, engine_config, output_dir="outputs/bt/btcusdt_1h")
result = runner.run_strategy(my_strategy_spec)
```

`CcxtBarDataConnector` exposes the exact interface `NautilusBacktestRunner`
expects:
- `connector.prepare_data()` → `list[Bar]`
- `connector.get_bar_type()` → `BarType`
- `connector.instrument` → Nautilus Instrument (attribute)

---

## 9. Saving outputs

```python
saved = connector.save_outputs()
# saved is a dict: artefact name → Path

# Typical saved files:
# outputs/ccxt/markets.json
# outputs/ccxt/binance/BTC_USDT_USDT/1m/raw_ohlcv.csv
# outputs/ccxt/binance/BTC_USDT_USDT/1m/raw_ohlcv.parquet
# outputs/ccxt/binance/BTC_USDT_USDT/1m/normalized_bars.parquet
# outputs/ccxt/binance/BTC_USDT_USDT/1m/connector_profile.json
```

`connector_profile.json` records exchange\_id, symbol, timeframe, venue,
`instrument_id`, `bar_type`, and `bars_count` for auditability.

---

## 10. Current limitations

1. **Single-symbol per BacktestRunner call.** `prepare_data()` returns bars
   for the first configured symbol only.  For multi-symbol backtests, create
   one connector per symbol and manage engine setup manually.

2. **Exchange metadata inconsistency.** Precision fields, contract sizes,
   fees, and linear/inverse flags vary by exchange version.  If
   `NautilusInstrumentFactory.build()` raises `NotImplementedError`, inspect
   the `missing_fields` list in the error and supply them via config overrides.

3. **Options not supported.** `market_type="option"` is detected but no
   Nautilus Instrument type is currently mapped for crypto options.

4. **No Nautilus ParquetDataCatalog write.** The connector writes plain
   Parquet via pandas.  To write into a Nautilus catalog use:
   ```python
   from nautilus_trader.persistence.catalog import ParquetDataCatalog
   catalog = ParquetDataCatalog("./catalog")
   catalog.write_data(connector.get_bars())
   ```

5. **Rate limits.** The ccxt rate limiter is synchronous.  For large
   downloads (years of minute bars) expect significant wall-clock time.
   Consider breaking the date range into monthly chunks and running in parallel.

---

## 11. Extending to live polling (future work)

When live polling is needed, the same `CcxtMarketConnector` and
`CcxtOhlcvConnector` can be driven in a polling loop:

```python
# Sketch only — not yet implemented
import time

connector = CcxtBarDataConnector(config_with_recent_since)
while True:
    connector._prepared = False          # force refresh
    connector.config.since = last_ts
    new_bars = connector.prepare_data()
    strategy.on_bars(new_bars)
    time.sleep(60)
```

For a production live feed, use `ccxt.pro` (async WebSocket API) and
implement a Nautilus `LiveDataClient`.  The `CcxtInstrumentMapper` and
`CcxtBarMapper` classes are reusable in both sync-polling and async-WebSocket
contexts.

---

## Module structure

```
nautilus_ext/ccxt/
    __init__.py              Public API exports
    ccxt_config.py           CcxtDataConfig dataclass
    ccxt_market_connector.py Exchange instantiation + market loading
    ccxt_ohlcv_connector.py  Paginated OHLCV download
    ccxt_instrument_mapper.py ccxt market → InstrumentProfile → Instrument
    ccxt_bar_mapper.py       OHLCV DataFrame → list[Bar]
    ccxt_cache.py            File-system output helpers
    ccxt_connector.py        CcxtBarDataConnector (high-level facade)

nautilus_ext/tests/
    test_ccxt_connector.py   Unit tests (all network-free, ccxt mocked)

docs/
    nautilus_ext_ccxt_connector.md   This document
```
