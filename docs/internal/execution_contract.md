# Execution Contract

This is the internal contract for generated and migrated strategies.

## Strategy layer

The strategy layer may:

1. Consume `FeatureSnapshot`.
2. Generate `BUY`, `SELL`, or `HOLD`.
3. Attach signal metadata that describes the decision.

The strategy layer must not:

1. Modify the executed position.
2. Modify cash or equity.
3. Set or override a fill price.
4. Treat a signal or order intent as an immediate fill.
5. Maintain the real position state.

A migrated legacy strategy may keep a pending target used to preserve signal
cadence. The pending target is not an executed position. Executed position and
fill prices are synchronized only from execution results.

## Execution layer

The execution layer owns:

1. `OrderIntent` and `PositionIntent` creation.
2. Concrete orders.
3. Fills and fill prices.
4. Executed positions.
5. Fees and commission.
6. Funding settlement.
7. Slippage.
8. Latency and fill timing.

The existing execution, accounting, and result artifact paths remain the only
paths for orders, fills, positions, PnL, and evaluation outputs.
