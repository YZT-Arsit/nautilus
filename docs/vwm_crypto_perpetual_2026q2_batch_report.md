# VWM Crypto-Perpetual Batch Report — 2026 Q2 (multi-symbol, 15m)

## 1. Purpose

Evaluate ONE strategy (VWM) across multiple Binance USD-M perpetuals under
identical conditions and present the boss deliverable: a horizontal table with
**rows = symbol, columns = evaluation metric**. This replaces the earlier
single-symbol BTCUSDT matrix as the primary comparison view (the matrix is kept
for internal analysis).

## 2. Why recent 2026 data

The previous 2024-06..08 results were development validation only and, being a
falling-market window, flattered a structurally short strategy. The final
deliverable uses a recent, complete three-month window so the comparison reflects
current market behaviour rather than a hand-picked down-market.

## 3. Data source

Public **read-only** Binance Vision archive
(`data/futures/um/daily/klines/<SYMBOL>/15m`). No API key, no private endpoint, no
account/balance/position/order access. Canonicalized to the project bar schema
(ts, instrument_id, OHLC, volume, quote_volume, trade_count, bar_source=trade_bar).

## 4. Symbols

BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT — BINANCE USD-M perpetual (`futures_um`),
instrument ids `*-PERP.BINANCE`.

## 5. Time window

- **requested_window:** 2026-03-01 → 2026-05-31
- **actual_window:** 2026-03-01 → 2026-05-31 (no fallback needed)
- **missing_days:** 0 for all four symbols
- **expected vs actual bars:** 92 days × 96 = **8832** expected; **8832 actual**
  for every symbol (monotonic timestamps, no duplicates, OHLC bounds valid,
  `bar_source=trade_bar`, consistent instrument_id). Not a 30d smoke — full quarter.

## 6. Bar type

15m only (one bar_type, multi-symbol). 5m was dropped from the comparison because
the prior matrix showed it is cost-dominated; per-period expansion is deferred to a
later phase.

## 7. Batch backtest command

```bat
:: ingest (once)
uv run --no-sync python scripts\ingest_crypto_perpetual_bars.py ^
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT --bar-type 15m ^
  --start 2026-03-01 --end 2026-05-31 --out-root historical_data\market_data ^
  --max-symbols 4 --max-days 92

:: batch backtest (single global window -> all 4 jobs in one run)
uv run --no-sync python scripts\run_vwm_batch_backtests.py ^
  --config configs\backtests\vwm_crypto_perpetual_2026q2_15m_batch.yaml ^
  --out outputs\backtests\vwm_crypto_perpetual_2026q2_15m_batch --fail-fast

:: build the rows=symbol table
uv run --no-sync python scripts\build_strategy_batch_eval_table.py ^
  --backtest-root outputs\backtests\vwm_crypto_perpetual_2026q2_15m_batch ^
  --data-root historical_data\market_data ^
  --out-dir outputs\backtests\vwm_crypto_perpetual_2026q2_15m_batch ^
  --strategy VWM --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT ^
  --bar-type 15m --start 2026-03-01 --end 2026-05-31 ^
  --vip-fee-ratio 0.2 --half-fee-ratio 0.5
```

4 jobs, 4 executed, **0 failures**, ~35s.

## 8. Table structure

**Rows = symbol, columns = evaluation metric.** Full CSV = 86 columns (basic /
returns / benchmark / risk / trade-quality / exposure / cost / data-quality /
run-status / perpetual-mechanism / caveat); compact MD = 20 columns.

## 9. Metric coverage

86 metrics = **57 implemented + 26 added + 3 planned**. Added this phase: annualized
return, fee drag, Calmar, return/maxDD, daily best/worst/avg/std, abs max drawdown,
downside vol, payoff, expectancy, median/best/worst trade, max consecutive win/loss,
net direction bias, net/gross, break-even commission, benchmark/strategy direction,
expected/actual bars + data-quality status, failure reason. Planned (NA, not faked):
Funding Data Available, Funding-adjusted Return, Mark Price Data Available — plus the
audit lists Beta/Correlation/IR, Notional Exposure, VaR/CVaR, Drawdown
Duration/Recovery as deferred. See `docs/strategy_evaluation_metric_coverage_audit.md`
and `batch_metric_coverage_audit.csv/md`.

## 10. Core evaluation table (rows = symbol)

| Symbol | Total Return | Benchmark | Excess | Zero-Fee | VIP Fee | Max DD % | Sharpe | Trades | Win | PF | Short % | Comm/Gross | Fee Drag |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | **−23.38%** | +10.12% | **−33.50%** | −13.84% | −15.75% | 25.09% | −4.70 | 130 | 31.5% | 0.70 | 44.4% | 0.69 | 9.54% |
| ETHUSDT | −0.49% | +2.29% | −2.78% | −0.20% | −0.26% | 0.63% | −2.49 | 134 | 29.1% | 0.86 | 44.4% | 1.47 | 0.29% |
| SOLUSDT | −0.03% | −2.67% | **+2.64%** | −0.01% | −0.02% | 0.03% | −3.38 | 146 | 32.2% | 0.79 | 44.6% | 0.86 | 0.01% |
| BNBUSDT | −0.11% | +15.20% | −15.32% | −0.03% | −0.05% | 0.13% | −2.68 | 127 | 31.5% | 0.87 | 42.9% | 2.01 | 0.08% |

(All 86 columns in `batch_evaluation_table.csv`; all four `Backtest Status = success`.)

## 11. Horizontal comparison observations

1. **Highest total return:** none positive; least-negative is SOLUSDT (−0.03%).
2. **Highest excess return:** SOLUSDT **+2.64%** — the only symbol beating its
   benchmark, and only because SOL was the only one that *fell* this quarter.
3. **Lowest max drawdown:** SOLUSDT (0.03%); **worst BTCUSDT (25.1%)**.
4. **Largest fee drag:** BTCUSDT (9.54%) — by far; others < 0.3%.
5. **Direction bias:** every symbol is **structurally short** (Long Trades = 0,
   short exposure ~43–45%). No long exposure anywhere.
6. **Largest trade count:** SOLUSDT (146); range 127–146 across symbols.
7. **All same direction?** Yes — all net short.
8. **Beat benchmark?** Only SOLUSDT (positive excess), regime-driven (SOL fell while
   BTC/ETH/BNB rose).
9. **Profitable only at zero fee?** No — zero-fee returns are also ≤ 0 for all four,
   so 2026 Q2 underperformance is a **signal/regime** issue, not merely cost. (ETH and
   BNB do show fees > gross, Comm/Gross 1.47 / 2.01 — cost-sensitive, but zero-fee is
   still ~flat-to-negative.)
10. **Worth parameter optimization?** Not yet. A short-only strategy in a quarter
    where most majors rose underperforms by construction; fix regime/direction
    handling first.

## 12. Caveats

- **Position sizing not normalized across symbols.** All jobs use a fixed
  `quantity = 1.0` contract. Because per-contract notional differs enormously
  (BTC ≫ ETH ≫ BNB ≫ SOL by price), BTCUSDT's −23% / 25% drawdown is largely a
  **notional-scale artifact**, not evidence it is a "worse" signal than the others.
  Cross-symbol *magnitudes* are therefore not capital-comparable; *excess vs each
  symbol's own benchmark* and *direction* are the comparable signals. (This is why
  Notional Exposure is a planned/NA metric.)
- **Regime dependence.** VWM here is structurally short; 2026 Q2 was mostly an
  up-quarter for majors, so it lost — the mirror image of the 2024 down-window where
  it "won". Neither proves alpha.
- **Perpetual mechanics not modeled:** funding, margin, liquidation, mark/index
  price. For a short carry over a quarter, funding alone could move the result
  materially. Results are a **screen**, not a performance verdict.
- Single strategy, single parameter set, single bar_type, single window.

## 13. Next steps

1. **Normalize position sizing** (equal notional or volatility-target per symbol) so
   cross-symbol magnitudes are comparable; re-run the batch. This is the single most
   important fix before reading anything into BTC vs the rest.
2. **Add a daily benchmark series** from the equity-curve close to unlock the planned
   Beta / Correlation / Information Ratio metrics.
3. **Model funding / margin / liquidation / mark price**, then re-check short-carry
   results over the quarter.
4. **Regime split / direction handling:** test VWM with a directional filter or a
   two-sided variant before any parameter optimization, given the pure-short bias.
5. Extend to more symbols (the table already supports it — add jobs to the config)
   and, in a separate phase, more bar_types.
