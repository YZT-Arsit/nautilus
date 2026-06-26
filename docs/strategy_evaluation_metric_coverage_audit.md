# Strategy Evaluation Metric Coverage Audit

Audits whether the VWM evaluation **metric system** (not data coverage, not signal
coverage) is complete enough to support a one-strategy × many-instruments batch
comparison. The metric system now lives in small modules
(`research/evaluation_metrics.py` math + `research/evaluation_tables.py` assembly)
and is rendered as a rows=symbol / cols=metric table by
`scripts/build_strategy_batch_eval_table.py`.

Per-metric machine-readable status is emitted alongside every run:

```
outputs/backtests/<run>/batch_metric_coverage_audit.csv
outputs/backtests/<run>/batch_metric_coverage_audit.md
```

Each metric carries: **status** (implemented / added / planned), **computed_from**,
**reliability** (reliable / approximate / unavailable), **reason**, **fallback**,
**included_in_csv**, **included_in_md**. Totals: **86 metrics = 57 implemented +
26 added + 3 planned**. Data source is limited to already-written backtest outputs
(`summary.json` / `equity_curve.csv` / `trades.csv`) plus the local bar parquet for
the benchmark. Nothing is downloaded, no backtest is re-run, nothing is fabricated.

Status definitions:
- **implemented** — produced by the prior single/matrix builders (pre-existing).
- **added** — newly computed this phase from existing outputs.
- **planned** — not reliably computable from current outputs (rendered `NA`).

---

## 1. Basic information
implemented: Strategy, Market Type, Exchange, Symbol, Contract Type, Bar Type,
Start, End, Days. **added:** Failure Reason (failed/missing reason).

## 2. Returns
implemented: Initial Cash, Final Equity, Net PnL, Total Return, Zero Fee Return,
Half Fee Return, VIP Fee 20% Return.
**added:** Annualized Return, Fee Drag (zero-fee − actual), Calmar Ratio
(annualized / maxdd%), Return / Max Drawdown, Best/Worst/Avg Daily Return, Daily
Return Std (all from equity-curve daily resample). Calmar / Return-over-DD are
**approximate** (NA when maxdd = 0); Annualized is approximate and NA on total loss.

## 3. Benchmark / relative
implemented: Benchmark Return (close-to-close B&H), Excess Return, Zero Fee Excess
Return. **added:** Benchmark Direction (up/down/flat), Strategy Direction Bias.
**planned:** Beta / Correlation / Information Ratio / up-vs-down-market split —
unavailable until a daily-aligned benchmark series is stored (the benchmark
currently uses only window first/last close). Listed here, not fabricated.

## 4. Risk
implemented: Max Drawdown %, Sharpe, Sortino, Volatility (annualized; Sharpe/
Sortino/Vol fall back to equity_stats — approximate). **added:** Max Drawdown
(absolute peak-trough), Downside Volatility. **planned:** VaR / CVaR / tail loss,
Drawdown Duration / Recovery Time — need a richer return distribution / drawdown-
interval pass; deferred (NA), not fabricated.

## 5. Trade quality
implemented: Trade Count, Fill Count, Long Trades, Short Trades, Win Rate, Profit
Factor, Avg Trade PnL, Avg Win, Avg Loss. **added:** Payoff Ratio, Expectancy,
Median Trade PnL, Best Trade, Worst Trade, Max Consecutive Wins, Max Consecutive
Losses (from trades.csv realized PnL).

## 6. Exposure / holding
implemented: Exposure %, Long/Short/Flat %, Avg/Max Holding Time, Avg/Max Holding
Bars. **added:** Net Direction Bias (long% − short%). **planned:** Average / Max
Position Size, Average / Max Notional Exposure — positions.csv is empty and the
equity-curve `position` is a contract count, not a stable notional; NA. *This gap
matters for cross-symbol comparison* (see report: fixed 1-contract size means BTC
notional ≫ ETH/SOL/BNB).

## 7. Cost sensitivity
implemented: Total Commission, Commission / Initial Cash, Commission / |Gross PnL|,
Commission / |Net PnL|, Avg Commission / Trade, Avg Commission / Fill, Break-even
Fee Ratio, Turnover (approximate). **added:** Net / Gross Ratio, Break-even
Commission. Fee scenarios (zero / half / VIP) live under Returns.

## 8. Data quality status
**added (whole category):** Expected Bars (days × bars/day), Actual Bars, Data
Quality Status (ok / partial / missing / extra / unknown). These are data-quality
fields, distinct from metric coverage; kept as auxiliary columns.

## 9. Run status
implemented: Backtest Status. **added:** Failure Reason. Missing symbols →
`missing_data`; ran-but-failed → `failed`; never dropped.

## 10. Perpetual mechanism
implemented (static = No): Funding Modeled, Margin Modeled, Liquidation Modeled,
Mark Price Modeled. **planned (NA / No, not fabricated):** Funding Data Available,
Funding-adjusted Return, Mark Price Data Available.

## 11. Caveat
implemented: Caveat string carrying the perpetual-mechanism gap plus a short-sample
note when days < 30.

---

## Summary

- **Reliably computable from current outputs** → implemented or added (categories
  1–9); all 83 such metrics are in the CSV, 20 in the compact MD.
- **Requires a daily benchmark series or a stable notional definition** (Beta /
  Correlation / IR, Notional Exposure, VaR / CVaR, Drawdown Duration / Recovery,
  Funding-adjusted) → **planned / NA**, each annotated in
  `batch_metric_coverage_audit.csv`, deferred until the data/definition exists.
- The system covers basic / returns / benchmark / risk / trade-quality / exposure /
  cost / data-quality / run-status / perpetual-mechanism — sufficient to support a
  single-strategy × multi-symbol batch screen. The 3 planned items are perpetual-
  mechanism modelling gaps, deliberately not faked.
