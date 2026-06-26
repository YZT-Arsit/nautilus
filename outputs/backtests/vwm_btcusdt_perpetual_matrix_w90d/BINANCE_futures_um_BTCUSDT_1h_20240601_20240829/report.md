# Backtest Report - BINANCE_futures_um_BTCUSDT_1h_20240601_20240829

- **Backend:** `nautilus_backtest`  **Mode:** `nautilus_native`  **Fill timing:** `same_bar`
- **Period:** 2024-06-01T00:00:00+00:00 -> 2024-08-29T23:00:00+00:00  (2160 bars)

## Metrics

| metric | value |
| --- | --- |
| initial_cash | 100000.00 |
| final_equity | 103350.15 |
| total_return | 3.3501% |
| max_drawdown | 10.8745% |
| realized_pnl (gross) | 5725.3000 |
| total_commission | 2234.3528 |
| net_realized_pnl | 3490.9473 |
| unrealized_pnl | -140.8000 |
| net_pnl | 3350.1472 |
| trade_count | 35 |
| win_rate (gross) | 34.29% |
| fill_count | 71 |
| signal_count (actionable) | 71 |
| bar_count | 2160 |

Signal breakdown: `{'HOLD': 2089, 'SELL': 36, 'BUY': 35}`

## Native engine summary

```json
{
  "engine": "BacktestEngine",
  "instrument_id": "BTCUSDT-PERP.BINANCE",
  "account_type": "MARGIN",
  "bars_loaded": 2160,
  "instrument_mapping": {
    "kind": "test_kit_factory",
    "metadata_source": "test_kit",
    "multiplier": null,
    "tick_size": null,
    "lot_size": null
  },
  "final_balance_quote": 103490.94725,
  "quote_currency": "USDT",
  "fills_captured": 71
}
```

## Trades

35 closed trade(s).

| side | qty | entry | exit | pnl | win |
| --- | --- | --- | --- | --- | --- |
| SHORT | 1 | 67577.4000 | 67979.4000 | -402.0000 | N |
| SHORT | 1 | 67863.6000 | 68460.4000 | -596.8000 | N |
| SHORT | 1 | 70767.9000 | 71298.5000 | -530.6000 | N |
| SHORT | 1 | 69186.5000 | 69513.0000 | -326.5000 | N |
| SHORT | 1 | 69325.3000 | 69998.9000 | -673.6000 | N |
| SHORT | 1 | 68285.8000 | 69340.1000 | -1054.3000 | N |
| SHORT | 1 | 66147.3000 | 66661.4000 | -514.1000 | N |
| SHORT | 1 | 65432.5000 | 65438.1000 | -5.6000 | N |
| SHORT | 1 | 64863.9000 | 64480.0000 | 383.9000 | Y |
| SHORT | 1 | 64166.5000 | 61358.6000 | 2807.9000 | Y |
| SHORT | 1 | 60926.9000 | 61049.9000 | -123.0000 | N |
| SHORT | 1 | 60831.6000 | 61550.5000 | -718.9000 | N |
| SHORT | 1 | 62715.6000 | 56327.3000 | 6388.3000 | Y |
| SHORT | 1 | 56040.0000 | 56750.0000 | -710.0000 | N |
| SHORT | 1 | 56846.1000 | 57498.6000 | -652.5000 | N |
| SHORT | 1 | 57658.0000 | 57540.5000 | 117.5000 | Y |
| SHORT | 1 | 57528.4000 | 57799.9000 | -271.5000 | N |
| SHORT | 1 | 62799.2000 | 64469.6000 | -1670.4000 | N |
| SHORT | 1 | 64997.1000 | 64644.8000 | 352.3000 | Y |
| SHORT | 1 | 63957.2000 | 65700.7000 | -1743.5000 | N |
| SHORT | 1 | 67073.5000 | 68100.0000 | -1026.5000 | N |
| SHORT | 1 | 66399.9000 | 66155.7000 | 244.2000 | Y |
| SHORT | 1 | 65624.0000 | 64549.3000 | 1074.7000 | Y |
| SHORT | 1 | 68067.2000 | 66656.1000 | 1411.1000 | Y |
| SHORT | 1 | 64577.4000 | 64628.5000 | -51.1000 | N |
| SHORT | 1 | 64160.9000 | 65208.2000 | -1047.3000 | N |
| SHORT | 1 | 63393.5000 | 55470.0000 | 7923.5000 | Y |
| SHORT | 1 | 55185.0000 | 56370.0000 | -1185.0000 | N |
| SHORT | 1 | 55545.4000 | 57031.7000 | -1486.3000 | N |
| SHORT | 1 | 60425.0000 | 59700.0000 | 725.0000 | Y |
| SHORT | 1 | 59468.5000 | 59385.9000 | 82.6000 | Y |
| SHORT | 1 | 57002.4000 | 59649.2000 | -2646.8000 | N |
| SHORT | 1 | 58783.7000 | 59486.0000 | -702.3000 | N |
| SHORT | 1 | 60296.0000 | 61045.0000 | -749.0000 | N |
| SHORT | 1 | 63705.0000 | 60603.1000 | 3101.9000 | Y |

## Final positions

| instrument | qty | avg | mark | uPnL |
| --- | --- | --- | --- | --- |
| BTCUSDT-PERP.BINANCE | -1 | 59182.4000 | 59323.2000 | -140.8000 |
