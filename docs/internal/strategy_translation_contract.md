# Strategy Translation Contract

This contract defines the interface boundary for translating a TBQuant strategy into the existing platform. It does not define execution implementation or strategy quality.

## Translation input and output

The translated strategy consumes the existing `FeatureSnapshot` contract and returns BUY, SELL, or HOLD plus optional signal metadata. A sized or multi-action strategy may return the existing intent-compatible action plan; it must not create a second order or fill model.

Strategy decision state and execution state are connected through the existing execution/state adapter boundary. Confirmed execution facts are read-only inputs to strategy decisions.

## Strategy responsibilities

Strategy is responsible for:

1. signal generation;
2. strategy decision state;
3. indicator-related decision state consumed through `FeatureSnapshot`;
4. market regime, pattern, stop/target formula, and requested sizing metadata.

Indicator computation remains in the existing `feature_engine`; translation must not reproduce indicators inside the strategy.

Strategy must not own or mutate:

1. actual position or filled quantity;
2. entry, add, reduce, or exit fill price;
3. cash;
4. realized or unrealized PnL;
5. commission or funding;
6. order status, partial fills, rejection state, or any order lifecycle state.

## Execution responsibilities

Execution is responsible for:

1. order and order lifecycle;
2. fill and fill price;
3. actual position and filled quantity;
4. fee;
5. funding;
6. cash and PnL;
7. configured slippage and latency.

## State translation rules

- Internal position gates translate to a read-only execution position view.
- Theoretical entry/exit prices translate to confirmed fill anchors.
- Market-derived stop/target formulas remain unchanged in strategy; execution owns their order and fill lifecycle.
- Position-age, cooldown, and favourable-excursion rules remain strategy policies but start and reset from confirmed fill transitions.
- Explicit virtual-trade models remain strategy decision state and must be named and stored separately from the actual position and PnL.
- Pyramid rules retain desired add conditions and requested sizing in strategy; confirmed entries, quantities, and add prices use `pyramid_fill_reconciliation`.

If a source strategy cannot be expressed by these boundaries, translation stops with `unknown`; it must not extend the execution core until a separate pattern audit establishes a reusable state model.
