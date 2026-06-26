# Backtest Report - BINANCE_futures_um_BTCUSDT_5m_20240601_20240607

- **Backend:** `nautilus_backtest`  **Mode:** `nautilus_native`  **Fill timing:** `same_bar`
- **Period:** 2024-06-01T00:00:00+00:00 -> 2024-06-07T23:55:00+00:00  (2016 bars)

## Metrics

| metric | value |
| --- | --- |
| initial_cash | 100000.00 |
| final_equity | 95937.40 |
| total_return | -4.0626% |
| max_drawdown | 6.1809% |
| realized_pnl (gross) | -1767.7000 |
| total_commission | 2294.9023 |
| net_realized_pnl | -4062.6022 |
| unrealized_pnl | 0.0000 |
| net_pnl | -4062.6022 |
| trade_count | 33 |
| win_rate (gross) | 15.15% |
| fill_count | 66 |
| signal_count (actionable) | 66 |
| bar_count | 2016 |

Signal breakdown: `{'HOLD': 1950, 'SELL': 33, 'BUY': 33}`

## Native engine summary

```json
{
  "engine": "BacktestEngine",
  "instrument_id": "BTCUSDT-PERP.BINANCE",
  "account_type": "MARGIN",
  "bars_loaded": 2016,
  "instrument_mapping": {
    "kind": "test_kit_factory",
    "metadata_source": "test_kit",
    "multiplier": null,
    "tick_size": null,
    "lot_size": null
  },
  "final_balance_quote": 95937.39775,
  "quote_currency": "USDT",
  "fills_captured": 66
}
```

## Trades

33 closed trade(s).

| side | qty | entry | exit | pnl | win |
| --- | --- | --- | --- | --- | --- |
| SHORT | 1 | 67540.1000 | 67680.1000 | -140.0000 | N |
| SHORT | 1 | 67641.3000 | 67780.8000 | -139.5000 | N |
| SHORT | 1 | 67661.0000 | 67768.7000 | -107.7000 | N |
| SHORT | 1 | 67670.0000 | 67747.1000 | -77.1000 | N |
| SHORT | 1 | 67728.2000 | 67770.2000 | -42.0000 | N |
| SHORT | 1 | 67762.3000 | 67829.6000 | -67.3000 | N |
| SHORT | 1 | 67771.9000 | 67874.9000 | -103.0000 | N |
| SHORT | 1 | 67789.9000 | 67835.5000 | -45.6000 | N |
| SHORT | 1 | 67700.1000 | 67716.5000 | -16.4000 | N |
| SHORT | 1 | 68019.8000 | 67952.4000 | 67.4000 | Y |
| SHORT | 1 | 67834.0000 | 67959.6000 | -125.6000 | N |
| SHORT | 1 | 68949.1000 | 69246.8000 | -297.7000 | N |
| SHORT | 1 | 69244.6000 | 69416.1000 | -171.5000 | N |
| SHORT | 1 | 69157.9000 | 69297.3000 | -139.4000 | N |
| SHORT | 1 | 69100.0000 | 69118.8000 | -18.8000 | N |
| SHORT | 1 | 69102.8000 | 69129.7000 | -26.9000 | N |
| SHORT | 1 | 68873.7000 | 68989.4000 | -115.7000 | N |
| SHORT | 1 | 70183.6000 | 70650.0000 | -466.4000 | N |
| SHORT | 1 | 70448.5000 | 70829.6000 | -381.1000 | N |
| SHORT | 1 | 70984.2000 | 71046.5000 | -62.3000 | N |
| SHORT | 1 | 70957.9000 | 70951.5000 | 6.4000 | Y |
| SHORT | 1 | 70824.0000 | 70992.4000 | -168.4000 | N |
| SHORT | 1 | 70870.4000 | 71025.0000 | -154.6000 | N |
| SHORT | 1 | 71218.9000 | 71231.8000 | -12.9000 | N |
| SHORT | 1 | 71100.0000 | 71198.0000 | -98.0000 | N |
| SHORT | 1 | 71063.6000 | 71059.4000 | 4.2000 | Y |
| SHORT | 1 | 70913.2000 | 71010.9000 | -97.7000 | N |
| SHORT | 1 | 71156.2000 | 71371.2000 | -215.0000 | N |
| SHORT | 1 | 71177.0000 | 70946.5000 | 230.5000 | Y |
| SHORT | 1 | 70790.3000 | 70870.0000 | -79.7000 | N |
| SHORT | 1 | 70726.6000 | 70817.0000 | -90.4000 | N |
| SHORT | 1 | 71136.7000 | 71205.9000 | -69.2000 | N |
| SHORT | 1 | 70920.6000 | 69466.9000 | 1453.7000 | Y |

## Final positions

Flat at end of run.
