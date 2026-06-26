# Backtest Report - BINANCE_futures_um_BTCUSDT_1h_20240601_20240607

- **Backend:** `nautilus_backtest`  **Mode:** `nautilus_native`  **Fill timing:** `same_bar`
- **Period:** 2024-06-01T00:00:00+00:00 -> 2024-06-07T23:00:00+00:00  (168 bars)

## Metrics

| metric | value |
| --- | --- |
| initial_cash | 100000.00 |
| final_equity | 98038.53 |
| total_return | -1.9615% |
| max_drawdown | 2.1224% |
| realized_pnl (gross) | -1529.4000 |
| total_commission | 241.5668 |
| net_realized_pnl | -1770.9669 |
| unrealized_pnl | -190.5000 |
| net_pnl | -1961.4669 |
| trade_count | 3 |
| win_rate (gross) | 0.00% |
| fill_count | 7 |
| signal_count (actionable) | 7 |
| bar_count | 168 |

Signal breakdown: `{'HOLD': 161, 'SELL': 4, 'BUY': 3}`

## Native engine summary

```json
{
  "engine": "BacktestEngine",
  "instrument_id": "BTCUSDT-PERP.BINANCE",
  "account_type": "MARGIN",
  "bars_loaded": 168,
  "instrument_mapping": {
    "kind": "test_kit_factory",
    "metadata_source": "test_kit",
    "multiplier": null,
    "tick_size": null,
    "lot_size": null
  },
  "final_balance_quote": 98229.03315,
  "quote_currency": "USDT",
  "fills_captured": 7
}
```

## Trades

3 closed trade(s).

| side | qty | entry | exit | pnl | win |
| --- | --- | --- | --- | --- | --- |
| SHORT | 1 | 67577.4000 | 67979.4000 | -402.0000 | N |
| SHORT | 1 | 67863.6000 | 68460.4000 | -596.8000 | N |
| SHORT | 1 | 70767.9000 | 71298.5000 | -530.6000 | N |

## Final positions

| instrument | qty | avg | mark | uPnL |
| --- | --- | --- | --- | --- |
| BTCUSDT-PERP.BINANCE | -1 | 69186.5000 | 69377.0000 | -190.5000 |
