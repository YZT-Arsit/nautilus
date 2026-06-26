# Backtest Report - BINANCE_futures_um_BTCUSDT_15m_20240601_20240607

- **Backend:** `nautilus_backtest`  **Mode:** `nautilus_native`  **Fill timing:** `same_bar`
- **Period:** 2024-06-01T00:00:00+00:00 -> 2024-06-07T23:45:00+00:00  (672 bars)

## Metrics

| metric | value |
| --- | --- |
| initial_cash | 100000.00 |
| final_equity | 97244.22 |
| total_return | -2.7558% |
| max_drawdown | 2.7558% |
| realized_pnl (gross) | -2063.7000 |
| total_commission | 692.0798 |
| net_realized_pnl | -2755.7798 |
| unrealized_pnl | 0.0000 |
| net_pnl | -2755.7798 |
| trade_count | 10 |
| win_rate (gross) | 20.00% |
| fill_count | 20 |
| signal_count (actionable) | 20 |
| bar_count | 672 |

Signal breakdown: `{'HOLD': 652, 'SELL': 10, 'BUY': 10}`

## Native engine summary

```json
{
  "engine": "BacktestEngine",
  "instrument_id": "BTCUSDT-PERP.BINANCE",
  "account_type": "MARGIN",
  "bars_loaded": 672,
  "instrument_mapping": {
    "kind": "test_kit_factory",
    "metadata_source": "test_kit",
    "multiplier": null,
    "tick_size": null,
    "lot_size": null
  },
  "final_balance_quote": 97244.22015,
  "quote_currency": "USDT",
  "fills_captured": 20
}
```

## Trades

10 closed trade(s).

| side | qty | entry | exit | pnl | win |
| --- | --- | --- | --- | --- | --- |
| SHORT | 1 | 67677.9000 | 67748.6000 | -70.7000 | N |
| SHORT | 1 | 67747.1000 | 67764.4000 | -17.3000 | N |
| SHORT | 1 | 67758.0000 | 67787.7000 | -29.7000 | N |
| SHORT | 1 | 67764.2000 | 67876.0000 | -111.8000 | N |
| SHORT | 1 | 67630.9000 | 68263.0000 | -632.1000 | N |
| SHORT | 1 | 69036.0000 | 68989.5000 | 46.5000 | Y |
| SHORT | 1 | 70825.6000 | 70895.8000 | -70.2000 | N |
| SHORT | 1 | 71072.8000 | 70992.4000 | 80.4000 | Y |
| SHORT | 1 | 70500.0000 | 71596.3000 | -1096.3000 | N |
| SHORT | 1 | 71035.5000 | 71198.0000 | -162.5000 | N |

## Final positions

Flat at end of run.
