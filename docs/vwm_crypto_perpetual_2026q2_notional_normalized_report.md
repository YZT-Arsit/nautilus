# VWM Crypto-Perpetual — Notional-Normalized Multi-Instrument Report (2026 Q2, 15m)

> **本阶段不是优化策略，也不是改变信号。只是把不同标的的初始名义仓位放到相同口径，
> 提升横向评测的公平性。** VWM signal logic, `feature_engine`, and `data_engine` are
> unchanged; only the per-job order quantity differs.

## 1. Why fixed quantity is unfair across symbols

The prior 2026 Q2 batch used a fixed `quantity = 1.0` contract for every symbol. At
window start, one contract of each symbol is a *very* different amount of money:

| Symbol | initial 15m close (2026-03-01) | notional of 1 contract |
|---|---:|---:|
| BTCUSDT | 66,883.4 | ~66,883 USDT |
| ETHUSDT | 1,961.34 | ~1,961 USDT |
| SOLUSDT | 84.65 | ~85 USDT |
| BNBUSDT | 617.09 | ~617 USDT |

So BTC's exposure was ~34× ETH, ~790× SOL. The fixed-quantity table's BTC −23.4% /
25% drawdown was therefore mostly a **position-scale artifact**, not evidence the BTC
*signal* was worse. Cross-symbol magnitudes were not comparable.

## 2. Normalization method

`sizing_method = initial_close_target_notional`:

```
order_quantity = target_notional_usdt / initial_price
initial_price  = close of the FIRST 15m bar of the window (read from local parquet)
```

Sizes are computed once at window start (not rebalanced). Per-symbol `quantity` is
written into the batch config and threaded by the runner into `execution.quantity`
(a config passthrough — **no VWM change**).

## 3. Target notional

`target_notional_usdt = 10,000` for every symbol.

## 4. Per-symbol sizing (from data, not hard-coded)

| Symbol | initial_price | order_quantity | actual_initial_notional |
|---|---:|---:|---:|
| BTCUSDT | 66,883.4 | 0.14951393 | 10,000.0 |
| ETHUSDT | 1,961.34 | 5.09855507 | 10,000.0 |
| SOLUSDT | 84.65 | 118.13349084 | 10,000.0 |
| BNBUSDT | 617.09 | 16.20509164 | 10,000.0 |

(`outputs/backtests/vwm_crypto_perpetual_2026q2_15m_notional_normalized/position_sizing.csv`)

## 5. Notional-normalized core table (rows = symbol)

| Symbol | Order Qty | Total Return | Benchmark | Excess | Zero-Fee | Max DD % | Sharpe | Trades | Win | PF | Short % | Comm/Gross | Fee Drag |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 0.1495 | −3.51% | +10.12% | −13.63% | −2.08% | 3.76% | −4.73 | 130 | 31.5% | 0.70 | 44.4% | 0.69 | 1.43% |
| ETHUSDT | 5.099 | −2.50% | +2.29% | −4.79% | −1.01% | 3.20% | −2.47 | 134 | 29.1% | 0.86 | 44.4% | 1.47 | 1.49% |
| SOLUSDT | 118.13 | −3.20% | −2.67% | −0.53% | −1.72% | 3.52% | −3.36 | 146 | 32.2% | 0.79 | 44.6% | 0.86 | 1.48% |
| BNBUSDT | 16.205 | −1.82% | +15.20% | −17.03% | −0.51% | 2.11% | −2.67 | 127 | 31.5% | 0.87 | 42.9% | 2.01 | 1.31% |

All four `Backtest Status = success`. Full 90 columns (86 + 4 sizing) in
`batch_evaluation_table.csv`.

## 6. Fixed vs normalized — the key difference

(`normalization_comparison.csv`)

| Symbol | Total Ret (fixed → norm) | Max DD % (fixed → norm) | Excess (fixed → norm) | Commission (fixed → norm) | Comm/Gross (fixed → norm) |
|---|---|---|---|---|---|
| BTCUSDT | −23.38% → **−3.51%** | 25.09% → **3.76%** | −33.50% → −13.63% | 9,537 → 1,430 | 0.689 → 0.689 |
| ETHUSDT | −0.49% → −2.50% | 0.63% → 3.20% | −2.78% → −4.79% | 291 → 1,486 | 1.469 → 1.469 |
| SOLUSDT | −0.03% → −3.20% | 0.03% → 3.52% | +2.64% → −0.53% | 13 → 1,481 | 0.862 → 0.862 |
| BNBUSDT | −0.11% → −1.82% | 0.13% → 2.11% | −15.32% → −17.03% | 81 → 1,313 | 2.011 → 2.011 |

Reading:
- **Fixed drawdowns spanned 0.03%–25.1%** (BTC dominated purely by notional);
  **normalized drawdowns are 2.1%–3.8%** — now genuinely comparable.
- **BTC's −23.4% / 25% DD collapses to −3.51% / 3.76%** under equal notional → it was a
  sizing artifact, **not** a worse signal. (Answer to "is the BTC scale bias fixed?":
  **yes.**)
- **Commission / |Gross PnL| is unchanged** (0.689 / 1.469 / 0.862 / 2.011) because
  scaling the order size scales commission and gross PnL by the same factor — a useful
  sanity check that normalization only rescales magnitudes, not the cost *ratio* or the
  signal. Win rate, profit factor, trade count, and exposure are likewise unchanged.

## 7. Did the strategy logic change? **No.** VWM signal math untouched.
## 8. Did feature_engine / data_engine change? **No.**

## 9. Caveats

- Sizing is fixed at the **window start** (initial close), not rebalanced; mid-window
  drift in relative notional remains. First-cut fairness, not a risk model.
- Still **structurally short** (Long Trades = 0); 2026 Q2 was an up-quarter for
  BTC/ETH/BNB, so all four lost vs their benchmarks (SOL closest to flat excess, −0.53%,
  because SOL fell). Normalization makes the comparison fair; it does not make the
  strategy profitable.
- **Funding / margin / liquidation / mark-index still not modeled** — for a short carry
  over a quarter, funding alone could move results materially. This remains a screen,
  not a verdict.

## 10. Next steps

1. **Volatility-targeted sizing** (size by ATR / realized vol, not just price) for an
   even fairer risk-parity comparison; rebalance periodically.
2. Add a **daily benchmark series** to unlock Beta / Correlation / Information Ratio.
3. **Model funding / margin / liquidation / mark price**, then re-check the short carry.
4. **Direction handling / regime filter** before any parameter optimization — the pure
   short bias is the dominant driver of the negative excess in an up-quarter.

---

### Artifacts
- config: `configs/backtests/vwm_crypto_perpetual_2026q2_15m_notional_normalized.yaml`
- sizing: `outputs/backtests/vwm_crypto_perpetual_2026q2_15m_notional_normalized/position_sizing.csv`
- table: `.../batch_evaluation_table.csv` + `.md`
- coverage: `.../batch_metric_coverage_audit.csv` + `.md`
- comparison: `.../normalization_comparison.csv`
- fixed-quantity baseline: `outputs/backtests/vwm_crypto_perpetual_2026q2_15m_batch/`
