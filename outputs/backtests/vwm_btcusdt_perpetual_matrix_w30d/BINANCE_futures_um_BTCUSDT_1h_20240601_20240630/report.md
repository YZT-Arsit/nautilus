# Backtest Report - BINANCE_futures_um_BTCUSDT_1h_20240601_20240630

- **Backend:** `nautilus_backtest`  **Mode:** `nautilus_native`  **Fill timing:** `same_bar`
- **Period:** 2024-06-01T00:00:00+00:00 -> 2024-06-30T23:00:00+00:00  (720 bars)

## Metrics

| metric | value |
| --- | --- |
| initial_cash | 100000.00 |
| final_equity | 97450.15 |
| total_return | -2.5499% |
| max_drawdown | 4.9521% |
| realized_pnl (gross) | -1753.6000 |
| total_commission | 796.2520 |
| net_realized_pnl | -2549.8520 |
| unrealized_pnl | 0.0000 |
| net_pnl | -2549.8520 |
| trade_count | 12 |
| win_rate (gross) | 16.67% |
| fill_count | 24 |
| signal_count (actionable) | 24 |
| bar_count | 720 |

Signal breakdown: `{'HOLD': 696, 'SELL': 12, 'BUY': 12}`

## Native engine summary

```json
{
  "engine": "BacktestEngine",
  "instrument_id": "BTCUSDT-PERP.BINANCE",
  "account_type": "MARGIN",
  "bars_loaded": 720,
  "instrument_mapping": {
    "kind": "test_kit_factory",
    "metadata_source": "test_kit",
    "multiplier": null,
    "tick_size": null,
    "lot_size": null
  },
  "final_balance_quote": 97450.148,
  "quote_currency": "USDT",
  "fills_captured": 24
}
```

## Trades

12 closed trade(s).

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

## Final positions

Flat at end of run.
