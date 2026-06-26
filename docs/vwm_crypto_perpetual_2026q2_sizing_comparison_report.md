# VWM Crypto-Perpetual — Sizing-Mode Comparison (2026 Q2, 15m)
## fixed_quantity vs notional_normalized vs volatility_targeted

> **仓位归一化改变的是交易规模，不是策略信号。** trade count / win rate / profit
> factor / direction bias 等信号相关指标在三种口径下保持一致；收益、回撤、手续费金额
> 随仓位缩放。VWM signal math, `feature_engine`, `data_engine` 均未改动。

## 1. Why fixed quantity is not comparable

`quantity = 1.0` means one contract of each symbol, but one contract is a very
different amount of money (BTC ~66,883 vs SOL ~85 USDT). BTC's exposure dwarfed the
rest, so its fixed −23.4% / 25% drawdown was a sizing artifact.

## 2. Why notional normalization is fairer

Equal **initial notional** (10,000 USDT each) removes the price-scale bias, so
magnitudes become comparable. BTC's drawdown fell from 25.1% to 3.8%.

## 3. Why volatility targeting improves risk comparability

Equal notional still leaves **different volatility**: a 10k position in a calm
symbol risks less per bar than 10k in a choppy one. Volatility targeting sizes each
symbol so it risks ~the same USDT per 15m bar:

```
realized_vol_15m = std of 15m log returns over the window
order_quantity   = target_risk_usdt_per_bar / (initial_price * realized_vol_15m)
```
(`sizing_method = realized_vol_target`, `target_risk_usdt_per_bar = 50`, sizes fixed
at window start; clamped to `[min_notional 1,000, max_notional 20,000]`.)

## 4. Per-symbol sizing (vol-targeted, from data)

| Symbol | realized_vol_15m | raw notional | final notional | final qty | sizing_status |
|---|---:|---:|---:|---:|---|
| BTCUSDT | 0.0022283 | 22,439 | **20,000** | 0.29902786 | **capped_max_notional** |
| ETHUSDT | 0.0028968 | 17,260 | 17,260 | 8.80035413 | ok |
| SOLUSDT | 0.0030300 | 16,502 | 16,502 | 194.94175331 | ok |
| BNBUSDT | 0.0020531 | 24,354 | **20,000** | 32.41018328 | **capped_max_notional** |

BTC and BNB have the **lowest** 15m vol → vol-targeting wants the largest notional →
both hit the 20,000 cap (honestly recorded, not silently clipped).

## 5. Vol-targeted core table (rows = symbol)

| Symbol | Order Qty | Notional | Total Return | Benchmark | Excess | Zero-Fee | Max DD % | Sharpe | Trades | Win | PF | Comm/Gross |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 0.2990 | 20,000 | −6.99% | +10.12% | −17.11% | −4.14% | 7.50% | −4.73 | 130 | 31.5% | 0.70 | 0.69 |
| ETHUSDT | 8.800 | 17,260 | −4.31% | +2.29% | −6.60% | −1.75% | 5.51% | −2.46 | 134 | 29.1% | 0.86 | 1.47 |
| SOLUSDT | 194.94 | 16,502 | −5.28% | −2.67% | −2.61% | −2.84% | 5.81% | −3.35 | 146 | 32.2% | 0.79 | 0.86 |
| BNBUSDT | 32.410 | 20,000 | −3.65% | +15.20% | −18.85% | −1.02% | 4.21% | −2.66 | 127 | 31.5% | 0.87 | 2.01 |

## 6. Fixed vs notional vs vol-targeted (`sizing_mode_comparison.csv`)

Total Return / Max DD % by sizing mode:

| Symbol | fixed Total | notional Total | **vol Total** | fixed DD | notional DD | **vol DD** |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | −23.38% | −3.51% | −6.99% | 25.09% | 3.76% | 7.50% |
| ETHUSDT | −0.49% | −2.50% | −4.31% | 0.63% | 3.20% | 5.51% |
| SOLUSDT | −0.03% | −3.20% | −5.28% | 0.03% | 3.52% | 5.81% |
| BNBUSDT | −0.11% | −1.82% | −3.65% | 0.13% | 2.11% | 4.21% |

**Signal-related metrics are invariant across all three modes** (per symbol):

| Symbol | Trades | Win Rate | Profit Factor | Short % | Comm/Gross | realized_vol_15m |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 130 | 31.5% | 0.698 | 44.4% | 0.689 | 0.00223 |
| ETHUSDT | 134 | 29.1% | 0.865 | 44.4% | 1.469 | 0.00290 |
| SOLUSDT | 146 | 32.2% | 0.786 | 44.6% | 0.862 | 0.00303 |
| BNBUSDT | 127 | 31.5% | 0.874 | 42.9% | 2.011 | 0.00205 |

Reading:
- **Fixed** drawdowns span 0.03%–25.1% (price-scale dominated). **Notional** brings
  them to 2.1%–3.8%. **Vol-targeted** brings the *per-bar risk* to a common budget
  (notionals 16.5k–20k; magnitudes larger because the risk-target notional exceeds 10k,
  and 2 symbols are capped).
- **Trade count, win rate, profit factor, exposure, short %, commission/|gross|, and
  realized vol are identical across the three modes** — proof that sizing rescales
  magnitudes only, **not the signal**. (Sharpe differs by < 0.04, from fixed slippage_bps
  interacting with scale; negligible.)
- All three modes agree on the verdict: VWM is structurally short, and 2026 Q2 was an
  up-quarter for BTC/ETH/BNB, so every symbol underperforms its benchmark; SOL (the only
  faller) has the least-negative excess.

## 7. Did the strategy signal change? **No.**
## 8. Did feature_engine / data_engine change? **No.**

## 9. Caveats

- Sizing is fixed at window start (initial price + window realized vol), **not
  rebalanced**; realized vol is a single full-window estimate.
- `target_risk_usdt_per_bar = 50` with these low 2026 Q2 vols (~0.002–0.003) implies
  16k–24k notionals, so the **20k cap binds for BTC and BNB** — perfect risk parity is
  not achieved for the capped symbols (reported as `capped_max_notional`).
- Still **structurally short**; normalization improves *comparability*, not
  profitability.
- **Funding / margin / liquidation / mark-index still not modeled** — a screen, not a
  verdict.

## 10. Next steps

1. **Rebalanced / rolling-vol targeting** (e.g. trailing ATR) instead of a single
   window estimate, and raise the notional cap if equal per-bar risk is the priority.
2. Add a **daily benchmark series** to unlock Beta / Correlation / Information Ratio.
3. **Model funding / margin / liquidation / mark price**, then re-check.
4. **Direction handling / regime filter** before any parameter optimization — the pure
   short bias drives the negative excess in an up-quarter regardless of sizing.

---

### Artifacts
- vol config: `configs/backtests/vwm_crypto_perpetual_2026q2_15m_vol_targeted.yaml`
- vol sizing: `outputs/backtests/vwm_crypto_perpetual_2026q2_15m_vol_targeted/position_sizing.csv`
- vol table: `.../vwm_crypto_perpetual_2026q2_15m_vol_targeted/batch_evaluation_table.csv` + `.md`
- vol coverage: `.../batch_metric_coverage_audit.csv` + `.md`
- 3-way comparison: `outputs/backtests/vwm_crypto_perpetual_2026q2_sizing_comparison/sizing_mode_comparison.csv` + `.md`
- baselines: `..._15m_batch/` (fixed), `..._15m_notional_normalized/` (notional)
