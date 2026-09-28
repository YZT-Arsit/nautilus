# Paper trading protocol v1

## 1. Objective

Continue frozen historical candidates forward on actual Binance USD-M public
market data without exchange order submission. The output is evidence for human
review, not a production trading decision.

## 2. Candidate freeze

`scripts/internal/prepare_paper_trading_v1.py` reads only the completed
historical selected-case manifest. It writes `paper_candidate_manifest.csv`, a
SHA-256 file, resolved configuration, repository commit and source-manifest
hash under an immutable `paper_YYYYMMDD_<hash>` experiment directory. Forward
performance cannot add, remove, reverse or reconfigure a candidate. Any change
requires a new experiment ID and forward start.

The initial manifest contains selected NORMAL variants. STRICT_REVERSE can enter
only as a separately frozen row selected before its forward start.

## 3. Data sources and recorder

The only permitted production connection is public, read-only market data:

- Binance USD-M aggregate trades;
- Binance USD-M L1 best bid/ask and sizes;
- funding-rate settlement events;
- timestamped exchange filters.

The repository Binance adapter supports L2 MBP, but v1 remains L1 BBO + trades
until a bounded continuity/recovery test passes. V1 is labelled
`L1_BBO_PAPER_MAKER`; it does not claim queue position.

`AppendOnlyMarketDataRecorder` persists normalized events by symbol and UTC
date. Each row contains deterministic event ID, exchange timestamp, local
receive timestamp, source and full normalized payload. Partition manifests
record row count, byte count and SHA-256. Duplicate event IDs are rejected.

## 4. Signal timing and bars

The existing registered strategy classes, indicators, parameters and position
mapping remain the signal source. There is no pandas signal implementation.
`CausalBarAggregator` builds `[t,t+T)` bars for 1m, 10m and 15m. A bar is emitted
only at or after `t+T`. Warm-up bars initialize indicators; they never enter
forward PnL.

The audit log records `ts_exchange`, `ts_receive`, `ts_strategy_decision`,
`ts_order_submit_simulated` and `ts_fill_simulated`, all in UTC nanoseconds.
Equal timestamps use deterministic priority: quote/book update, trade, funding,
then deterministic ID. Exchange sequence is used when present.

## 5. Execution modes

Each candidate has isolated accounts for both modes.

### FIRST_TICK_SHADOW

The target is filled in full at the first production trade whose exchange
timestamp is at or after the completed-bar decision. It is an idealized
benchmark and never creates an exchange order.

### MAKER_PAPER

The local Nautilus `OrderMatchingEngine` receives normalized QuoteTick and
TradeTick equivalents. The order is `LIMIT`, `post_only=True`, BUY at current
best bid and SELL at current best ask. The frozen lifecycle is
`GTC_UNTIL_SIGNAL_INVALID`: a remainder rests while the target is valid and is
cancelled on flat, material target change or reversal. The replacement delta is
computed from actual OrderFilled-based position. Partial fills persist; no fill
does not move position; taker fallback is forbidden.

## 6. Latency

Market-data receive delay and strategy processing time are measured. Exchange
order-path latency is `ORDER_LATENCY_UNCALIBRATED` until measured independently.
V1 does not invent a production latency. A future sensitivity must be
predeclared in a new config, not selected from profitability.

## 7. Fees, funding and account model

V1 freezes capital at 100,000 USDT, leverage 1.0 and target notional 100,000
USDT per isolated candidate account. Primary diagnostics use explicit
`GROSS_DIAGNOSTIC_V1` zero fees because no account tier was supplied. A sourced
fee config can be added only as a new version.

Funding uses Binance historical/forward funding-rate events and the actual
executed position at settlement. Positive funding means longs pay shorts:
`payment = -position_qty × mark_price × funding_rate`. Premium Index is never
booked as PnL. Fee and funding IDs are idempotent and booked exactly once.

`ExchangeFilter` versions tick size, step size, minimum quantity and minimum
notional. Buy prices round down to stay passive; sell prices round up. Quantity
rounds down. Invalid or sub-minimum orders remain unfilled and are logged.

## 8. State, restart and idempotency

Atomic checkpoints preserve account, actual positions, open simulated orders,
partial fills, indicator state references, latest processed event/sequence and
all booked IDs. Signal, order, fill, cancel, fee and funding IDs derive from
stable inputs. Replaying a partition cannot double-book a fill or cashflow.

The audit log is append-only. Restart loads the latest checkpoint, replays only
unseen deterministic IDs and reconciles position from fills before accepting
new simulated orders.

## 9. Gaps and kill switches

Sequence regression, out-of-order timestamps, stale BBO, missing events, clock
drift above tolerance, duplicate fill, invalid metadata or accounting mismatch
blocks new simulated orders. The system continues recording diagnostics and
marks the interval `DATA_GAP`; it never trades from a stale quote.

Production order submission is structurally absent. The configured safety
boundary always raises, credentials are forbidden and no execution endpoint is
configured.

## 10. Turnover and metrics

Turnover uses actual executed notional. UTC daily turnover sums executed
position/notional changes within each complete UTC day. Zero-turnover complete
days remain in the headline average. Reports include mean, median and P95 daily
turnover plus total turnover. Signed BE remains gross return × 10,000 divided
by total raw turnover; daily turnover never replaces its denominator.

Forward outputs include return, daily return, UTC-daily Sharpe using sample SD
and √365, daily observation count, MaxDD, BE, turnover, funding, fees, actual
exposure and maker execution diagnostics. Mechanical checkpoints are 7, 30,
90, 180 days and since inception. They are not validation thresholds.

## 11. Validation and release stages

- P0: code, resolved config and deterministic unit tests.
- P1: run the same persisted event partition twice; signals, orders, fills,
  PnL and daily turnover digests must match exactly. Historical-paper parity is
  reconciled on the server fixture before P2.
- P2: bounded 24-hour production public-market-data dry run with local
  simulation. Verify continuity, memory/disk growth, signals, fills, restart,
  funding if encountered and daily turnover. This is engineering evidence.
- P3: seven-day forward observation. It is not started automatically and
  requires explicit review after P2.

No short forward sample is called live-validated or production-ready.

## 12. Storage and versioning

The root is `D:\nautilus\paper_trading`. Market data and immutable experiment
directories remain separate from historical outputs. Each experiment contains
manifest, strategy state, orders, fills, daily metrics, figures and audit log.
An experiment is never overwritten.

## 13. Limitations

L1 BBO does not reveal queue ahead. Order-path latency is uncalibrated. Zero-fee
diagnostics are not an account-tier claim. Forward Sharpe is unstable at small
daily sample sizes. L2 and any future live adapter require separate engineering
validation; neither changes this experiment retrospectively.
