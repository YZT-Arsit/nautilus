# Nautilus backtest backend

How a full backtest runs end-to-end through the **existing** layered architecture
and into a **real** Nautilus `BacktestEngine` — without a parallel "lightweight"
backtester, and without leaking Nautilus into the feature/strategy layers.

## 1. Full execution chain

```
data_engine.load_events(data_config)              # synthetic / csv_bars / parquet_bars
    -> feature_engine                             # FeatureSpec -> BackendRegistry -> PythonBackend
    -> strategies/<name> (on_snapshot)            # signal: BUY / SELL / HOLD
    -> strategy_framework.execution.SignalToOrderPolicy
    -> OrderIntent / PositionIntent               # dependency-free intent model
    -> strategy_framework.backends.NautilusBacktestBackend
         - mode=simulated:       IntentFillSimulator           (no Nautilus)
         - mode=nautilus_native: nautilus_native.run_native_backtest -> Nautilus BacktestEngine
    -> strategy_framework.execution.backtest_report.write_backtest_report
    -> outputs/backtests/<run_name>/
         config.yaml  signals.csv  intents.csv  fills.csv
         trades.csv   positions.csv  equity_curve.csv
         metrics.json  report.md
```

`run_strategy.py` is unchanged in spirit: it loads a config, looks up the plugin,
builds the `FeatureStrategyRunner`, loads events, and per event calls
`backend.on_signal(event, snapshot, signal)`, then `backend.close()`. The backend
writes the report directory on `close()`.

## 2. Feature registration is unchanged

The feature path is **not** modified by this work:

```
StrategyPlugin.build_specs(config) -> list[FeatureSpec]
    -> SpecFeatureEngine
    -> BackendRegistry
    -> PythonBackend  (dispatch by params["type"], else by feature-name prefix)
    -> concrete Feature class
```

Adding a strategy still means: `strategies/<name>/` + a `PLUGIN` registered in
`strategy_framework/registry.py` + a config. No new feature registry, no change to
`FeatureSpec` / `BackendRegistry` / `PythonBackend` dispatch.

## 3. Where Nautilus lives (and does not)

| Layer | Imports `nautilus_trader`? |
|-------|----------------------------|
| `data_engine` | No |
| `feature_engine` (incl. `compute/`) | No |
| `strategies/<name>` | No |
| `strategy_framework.execution.*` | No |
| `strategy_framework/backends/nautilus_backtest.py` | No (top level) |
| `strategy_framework/backends/nautilus_native.py` | **Yes — lazily, inside the run function only** |

The single Nautilus boundary is `nautilus_native.py`. Importing it never requires
Nautilus; the imports happen inside `run_native_backtest(...)`. Boundary tests
assert the execution and strategy layers stay Nautilus-free.

## 4. The native adapter (`nautilus_native.run_native_backtest`)

Translation only — no feature maths, no data-file reads:

- **bars**: internal `BarEvent` dicts → pandas DataFrame (UTC `timestamp` index,
  OHLCV columns) → `BarDataWrangler(bar_type, instrument).process(df)` → `list[Bar]`.
- **instrument**: mapped from `data.instrument_id` via `TestInstrumentProvider`
  (MVP: `BTCUSDT.BINANCE`, `ETHUSDT.BINANCE`).
- **intents → orders**: a thin `_IntentReplayStrategy(Strategy)` subscribes to the
  bar type and, on each bar, looks up the pre-computed intent by `bar.ts_event` and
  submits a market order (`BUY`, short `SELL`, or `FLAT` = reduce-only close). It
  computes **no** signals; it only re-emits decisions already made upstream.
- **fills**: captured in `on_order_filled(event)` from the well-defined
  `OrderFilled` fields (`order_side`, `last_qty`, `last_px`, `commission`,
  `ts_event`) into internal `FillRecord`s.
- **account**: `engine.add_venue(...)` with `AccountType.CASH` (or `MARGIN` when
  `allow_short`), starting balance `Money(initial_cash, quote_currency)`; the final
  balance is reported in `metrics["engine"]`.

After `engine.run()`, fills + the bar marks flow into the shared report writer.

## 5. The shared report writer (`execution/backtest_report.py`)

Dependency-free (stdlib `csv`/`json`; `yaml` opportunistic for `config.yaml`).
Given bar marks, signals, intents, and **fills from either source**, it does
mark-to-market accounting — cash, positions, realized/unrealized PnL, an equity
curve, and round-trip trades — and writes the artifact set.

> This is reporting/analytics on top of fills, **not** a matching engine. The
> match/fill decision is owned by `IntentFillSimulator` (reference) or the Nautilus
> `BacktestEngine` (native). There is no second backtest engine.

### metrics.json

`total_return`, `max_drawdown`, `trade_count`, `win_rate`, `final_equity`,
`initial_cash`, `realized_pnl`, `unrealized_pnl`, `fill_count`, `bar_count`,
`signal_count` (actionable), `signal_breakdown`, `start_time(_ns)`,
`end_time(_ns)`, plus `engine` (native account summary) when applicable.

## 6. Config

```yaml
run_name: ma_crossover_nautilus_synthetic
strategy: ma_crossover
params: { fast_window: 5, slow_window: 20, input_type: bar, input_field: close }
data:   { mode: synthetic, instrument_id: BTCUSDT.BINANCE, warmup_bars: 20, live_bars: 20 }
execution:
  backend: nautilus_backtest
  mode: nautilus_native        # or "simulated"
  initial_cash: 100000
  quantity: 1.0
  sell_means: flat
  allow_short: false
  price_field: close
  fee_rate: 0.0005
  slippage_bps: 1.0
output: { root: outputs/backtests, print_table: false }
```

Data still enters only through `data_engine.load_events(...)`; the Nautilus
backend never reads raw files directly. Parquet works the same way (load via
`data_engine`, then the bars flow into the backend unchanged).

## 7. Current MVP limitations

- Single instrument per run; mapped Binance spot pairs only (extend the map in
  `nautilus_native.py`).
- Market orders, one bar type (`1-MINUTE-LAST-EXTERNAL`); `sell_means="flat"`
  closes the long. Native shorting needs a `MARGIN` account (`allow_short: true`).
- Native fills follow the engine's bar fill model (a market order submitted on a
  bar fills on the following bar); an intent on the very last bar may not fill.
  The equity curve uses the engine's actual fills.
- `fee_rate` / `slippage_bps` are applied in the **report** accounting; a native
  Nautilus commission/fill model is future work.
- The native run replays the **live** bar window (where signals occur), not the
  warmup bars.

## 8. Future extensions

Multi-instrument and multi-bar-type runs; a real Nautilus commission/slippage
model; production data (Parquet / Binance Vision via `data_engine`); a live
backend (`nautilus_live`).

## 9. Tests

- `nautilus_ext/tests/test_nautilus_backtest_backend.py` — report math, simulated
  artifacts, intent mapping, lazy-import + clear-error guards, layer boundaries,
  feature-registration-intact (no network, no Nautilus).
- `nautilus_ext/tests/test_run_strategy_nautilus_smoke.py` — full `run_strategy`
  smoke in simulated mode; native mode guarded by `importorskip("nautilus_trader")`
  so it runs on the backtest server and is skipped where Nautilus is not built.

> Note: framework tests live under `nautilus_ext/tests/` (the repo's existing
> convention). The top-level `tests/` directory is vendored **nautilus_trader**'s
> own suite and its `conftest.py` imports `nautilus_trader` at module load.
