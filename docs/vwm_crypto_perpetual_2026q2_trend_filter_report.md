# VWM Trend-Filter Experiment Report (2026 Q2, 15m, vol-targeted)

> **Trend filter 是一个可解释的 regime gate，不是参数优化，也不是新策略。** 它用于验证
> VWM short-only 信号是否需要行情方向约束。`enable_trend_filter=false` 时与原 baseline
> 完全一致;VWM 信号数学、`feature_engine`、`data_engine` 均未改动。

## 1. Direction-bias audit summary

`strategies/vwm_short/strategy.py` + `VolumeWeightedMomentumShortSignalEngine` emit
only `enter_short` / `exit_short` — **VWM here is short-only** (Long Trades = 0,
short exposure ~44%). Full audit: `docs/vwm_direction_bias_audit.md`.

## 2. Is the current strategy short-only? **Yes.**

## 3. Why short-only lags an up-quarter

2026 Q2 benchmarks: BTC +10.1%, ETH +2.3%, BNB +15.2% (up), SOL −2.7% (down). A
short-only strategy is negatively coupled to the benchmark, so it fights three of
four rising majors and systematically lags. Cost was already ruled out (zero-fee
returns also negative). This is a **direction/regime** problem.

## 4. Filter design

A config-gated **higher-timeframe trend gate** on short entries only. The VWM
signal is unchanged; when the engine produces `enter_short`, the gate decides
whether it is allowed:

```
fast_ma = SMA(close, 96)     # ~1 day of 15m bars
slow_ma = SMA(close, 384)    # ~4 days
allow short  iff  fast_ma < slow_ma          (downtrend)
block short  iff  fast_ma >= slow_ma          (uptrend)
warmup (insufficient history) -> conservatively block
```

Implemented as pure helpers (`trend_gate` / `should_block_short_entry`) +
`VwmShortConfig` fields; no change to entry/exit math, indicators, or engines.

## 5. Filter parameters

| param | value |
|---|---|
| `enable_trend_filter` | true (baseline: false) |
| `trend_filter_fast_len` | 96 |
| `trend_filter_slow_len` | 384 |
| `trend_filter_mode` | short_only_downtrend |
| `trend_filter_source` | close |

## 6. Did `enable_trend_filter=false` equal baseline? **Yes.**

`should_block_short_entry(..., enabled=False)` returns `False` unconditionally — the
gate is inert when off, so the strategy is bit-for-bit the baseline. Proven by
`test_vwm_trend_filter.py::test_should_block_disabled_is_baseline` and the unchanged
baseline regression suite.

## 7. Filtered evaluation table (rows = symbol; sizing = vol-targeted)

| Symbol | Total | Benchmark | Excess | Max DD % | Sharpe | Trades | Win | PF | Short % | Filter |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| BTCUSDT | −1.62% | +10.12% | −11.74% | 2.81% | −1.66 | 52 | 30.8% | 0.89 | 19.2% | on |
| ETHUSDT | −2.80% | +2.29% | −5.09% | 3.66% | −2.30 | 61 | 21.3% | 0.73 | 19.0% | on |
| SOLUSDT | −2.99% | −2.67% | −0.32% | 3.79% | −2.86 | 64 | 29.7% | 0.63 | 19.3% | on |
| BNBUSDT | −2.08% | +15.20% | −17.28% | 2.95% | −2.63 | 50 | 30.0% | 0.70 | 16.0% | on |

(`Filtered Entry Count` / `Blocked Entry Count` / `Filter Block Rate` are
strategy-internal and not surfaced to summary.json → recorded `NA`, not fabricated.
The observable proxy is the trade-count / short-exposure reduction below.)

## 8. Baseline (vol-targeted) vs filtered (`trend_filter_comparison.csv`)

| Symbol | Total (base→filt, Δ) | Excess (base→filt, Δ) | Max DD % (base→filt, Δ) | Trades (Δ) | Short % (base→filt, Δ) |
|---|---|---|---|---|---|
| BTCUSDT | −6.99%→−1.62% (**+5.38**) | −17.11%→−11.74% (**+5.38**) | 7.50%→2.81% (**−4.70**) | 130→52 (−78) | 44.4%→19.2% (−25.2) |
| ETHUSDT | −4.31%→−2.80% (+1.51) | −6.60%→−5.09% (+1.51) | 5.51%→3.66% (−1.85) | 134→61 (−73) | 44.4%→19.0% (−25.4) |
| SOLUSDT | −5.28%→−2.99% (+2.29) | −2.61%→−0.32% (+2.29) | 5.81%→3.79% (−2.02) | 146→64 (−82) | 44.6%→19.3% (−25.3) |
| BNBUSDT | −3.65%→−2.08% (+1.57) | −18.85%→−17.28% (+1.57) | 4.21%→2.95% (−1.26) | 127→50 (−77) | 42.9%→16.0% (−27.0) |

## 9–11. Effect of the filter

- **8. Reduced wrong-direction exposure?** **Yes** — short exposure ~44% → 16–19%
  (Δ ≈ −25 pp) on every symbol; the gate removed shorts taken in uptrends.
- **9. Improved total / excess return?** **Yes, all four** (Δ total = +1.5% to +5.4%;
  excess improves by the same amount, benchmarks fixed). SOL's excess goes nearly to
  flat (−0.32%).
- **10. Lower max drawdown?** **Yes, all four** (Δ = −1.3 to −4.7 pp; BTC 7.50%→2.81%).
- **11. Lower trade count / fee drag?** **Yes** — trades roughly halved (−73 to −82),
  fee drag roughly halved (e.g. BTC 2.85%→1.14%).
- **Profit factor is mixed:** BTC improves (0.70→0.89) but ETH/SOL/BNB decline
  (0.86→0.73 / 0.79→0.63 / 0.87→0.70). Fewer, regime-aligned shorts cut the bleeding
  and lift returns, but the surviving downtrend shorts are not uniformly higher-PF —
  reported honestly, not smoothed.

## 12. Did the VWM signal change? **No — entry/exit math untouched; only entry gated.**
## 13. feature_engine / data_engine changed? **No.**

## 13. Caveats

- The gate **improves but does not fix** the up-quarter underperformance: all four
  excess returns are still negative (the strategy remains short-only and three majors
  rose). It validates the *direction* hypothesis, not a profitable strategy.
- Single window, single parameter set (96/384), fixed at window start. Not a
  parameter search.
- **Funding / margin / liquidation / mark-index still not modeled.**

## 14. Next steps

1. **Small robustness sweep** of `fast/slow` (e.g. 48/192, 96/384, 192/768) on the
   same window to check the gate isn't curve-fit to 96/384.
2. **Add a long leg** (symmetric VWM long in uptrends) — the bigger structural fix,
   to actually participate in rising markets rather than only sitting out.
3. Test across **multiple regimes / windows** (an up window and a down window) to
   confirm the gate helps in up-markets without hurting down-markets.
4. Then model **funding / margin / liquidation / mark price** before any verdict.

---

### Artifacts
- filter config: `configs/backtests/vwm_crypto_perpetual_2026q2_15m_vol_targeted_trend_filtered.yaml`
- filtered table: `outputs/backtests/vwm_crypto_perpetual_2026q2_15m_vol_targeted_trend_filtered/batch_evaluation_table.csv` + `.md`
- comparison: `outputs/backtests/vwm_crypto_perpetual_2026q2_trend_filter_comparison/trend_filter_comparison.csv` + `.md`
- baseline: `outputs/backtests/vwm_crypto_perpetual_2026q2_15m_vol_targeted/`
- audit: `docs/vwm_direction_bias_audit.md`
