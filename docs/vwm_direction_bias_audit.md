# VWM Direction-Bias Audit

Audits why the 2026 Q2 VWM batch is 100% short across BTCUSDT / ETHUSDT / SOLUSDT /
BNBUSDT, and why a short-only strategy systematically underperforms in an
up-quarter. **No strategy change is made in this audit** — it is diagnosis only.

## 1. VWM entry rule (as wired)

The batch runs `strategies/vwm_short/strategy.py` → `VwmShortStrategy`, which drives
`VolumeWeightedMomentumShortSignalEngine`
(`nautilus_ext/strategies/vwm_short_signals.py`). The engine emits exactly two
reasons: `enter_short` and `exit_short` — it maps the TradeBlazer VWM short setup
(BearSetup = CrossUnder(VWM, 0); entry when `Low <= SEPrice[1] − ATRPcnt·AATR[1]`
within the setup window). `VwmShortStrategy.on_snapshot` returns `SELL` to open the
short and `BUY` to close it.

## 2. Does a long condition exist?

**No.** `grep` over `vwm_short_signals.py` shows only `enter_short` / `exit_short`
(SELL entry, BUY exit). There is no `enter_long` / `exit_long`, no BUY-to-open path.

## 3. Is the short condition "easier" to trigger?

It is the *only* condition. There is no symmetric long setup, so the strategy can
only ever be flat or short.

## 4. Short-only or bidirectional?

**Short-only.** `VwmShortStrategy` + `VolumeWeightedMomentumShortSignalEngine` is a
one-sided short strategy by construction.

## 5. Is `strategies/vwm_short/strategy.py` naturally short-only?

Yes — it only translates `enter_short`→SELL and `exit_short`→BUY (close). It never
opens a long.

## 6. Does the batch config only call the short version?

Yes. Every batch config sets `strategy.name: vwm` → the `vwm_short` plugin
(`strategy_framework/registry.py`). There is no other VWM variant invoked.

## 7. Is there a long / bidirectional version?

No long or bidirectional VWM variant exists in the repo. Building one is out of
scope for this phase (would be a new strategy, not a minimal change).

## 8. Signal vs benchmark direction

A short-only strategy profits when price falls and loses when price rises. Its
return is therefore *negatively* coupled to the benchmark: it tends to beat a
falling benchmark and lag a rising one.

## 9. 2026 Q2 benchmark direction (per symbol, 15m close-to-close)

| Symbol | Benchmark Return | Direction |
|---|---:|---|
| BTCUSDT | +10.12% | up |
| ETHUSDT | +2.29% | up |
| SOLUSDT | −2.67% | down |
| BNBUSDT | +15.20% | up |

Three of four majors rose over the window.

## 10. Current exposure (vol-targeted baseline)

All four symbols: **Long exposure 0%, Short exposure ~43–45%** (Long Trades = 0).
Structurally one-sided short.

## 11. Why short-only systematically lags an up-quarter

With benchmarks up for BTC/ETH/BNB and the strategy able only to short, every short
fights the trend: shorts opened in an uptrend tend to lose, and there is no long
leg to capture the rise. The one symbol that fell (SOL) has the least-negative
excess. So the 2026 Q2 underperformance is **a direction/regime problem, not a
sizing or cost problem** (cost was addressed in the prior sizing phases; zero-fee
returns were also negative).

## Conclusion

> 当前不是 VWM 双向策略，而是 VWM short-only strategy。2026 Q2 多数标的上涨时，
> 该策略天然容易跑输 benchmark。

This motivates a **minimal, config-gated regime filter** (next step): only allow a
short entry when the market is in a downtrend (`fast_ma < slow_ma`), default-off so
the baseline is unchanged. It is a diagnosis-driven gate, not a new strategy or a
parameter optimization.
