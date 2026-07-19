# State Ownership Contract

This contract defines the ownership boundary used when migrating legacy strategy state. It does not change the execution contract or strategy signal rules.

## Ownership rules

Strategy owns decision state only. It may retain:

- indicator state and rolling inputs;
- signal state and per-bar decision guards;
- market-regime state;
- pattern state, including explicitly simulated or virtual trades used only as a signal filter.

Strategy must not own or mutate:

- actual position or filled quantity;
- cash or account equity derived from execution;
- entry or exit fill price;
- commission or funding;
- order status, partial-fill state, rejection state, or any other order lifecycle state.

Execution owns:

- orders, fills, and order lifecycle;
- actual position and filled quantity;
- cash and PnL;
- fees and funding;
- execution prices, including slippage and latency effects.

State is classified by meaning, not by its variable name. For example, Ghost Trader's `my_position` is a virtual-trade pattern state and remains strategy-owned; its real `position` is execution-owned.

## State classifications

- `strategy_decision_state`: determined from market/features or prior strategy decisions and used to decide BUY/SELL/HOLD. It remains in the strategy.
- `execution_state`: a fact created by orders or fills. It moves to execution and is exposed read-only to the strategy when required.
- `mixed_state`: a strategy rule whose activation or anchor depends on an execution fact. It is split: execution owns the fact, while strategy owns the rule derived from that fact.

## Reusable B-class migration patterns

The B-class audit found five reusable patterns:

1. `position_gate`: replace internal actual-position mutation with a read-only execution position view.
2. `fill_anchored_price`: keep the entry/exit rule, but source its price anchor from confirmed fills.
3. `market_derived_stop_target`: keep stop/target formulas and market-derived levels in strategy; execution owns order activation and fills.
4. `filled_position_lifecycle`: keep policy counters and favourable-excursion logic, but start/reset them only from confirmed fill transitions.
5. `virtual_trade_decision_model`: retain an explicitly simulated trade model used only for signal generation, while separating it from the actual execution position.

The 57 B-class strategies use one or more of these patterns. No B-class strategy is migrated by this design phase. Turtle is the C-class representative for pyramid state because the B-class inventory contains no equivalent multi-entry state pattern.

## StrategyStateAdapter design

`StrategyStateAdapter` is a migration boundary, not a second execution system. Its conceptual interface has four responsibilities:

1. Read the legacy strategy's decision state without changing signal conditions.
2. Translate BUY/SELL/HOLD plus signal metadata into information required by the existing execution contract.
3. Consume confirmed order/fill/position snapshots and expose only the read-only execution facts needed by signal logic.
4. Synchronize mixed-state activation/reset points on confirmed fills so signal parity can be checked explicitly.

The adapter must not calculate indicators, place or fill orders, calculate PnL, fees, or funding, or create a second position ledger.

### State retained by strategy

- indicator buffers and derived market state;
- regime, pattern, breakout, and prior-signal flags;
- stop/target formulas and market-derived trigger levels;
- risk-unit and desired-size formulas;
- virtual-trade state that is explicitly part of signal generation;
- per-bar deduplication guards and policy counters.

### State supplied by execution

- actual position direction and filled quantity;
- confirmed entry, add, reduce, and exit fill prices;
- average position price where required by the rule;
- pending, partial, filled, cancelled, and rejected order status;
- cash, realized/unrealized PnL, commission, and funding.

### Mixed state split

- Fill-anchored stop/target: execution supplies the confirmed fill anchor; strategy retains the formula.
- Pyramiding: strategy retains add conditions and desired unit count; execution supplies confirmed add fills and actual quantity.
- Favourable excursion: strategy retains the market-path calculation; it begins only after an entry fill and resets after an exit fill.
- Position age/cooldown: strategy retains the counter policy; fill transitions determine start and reset.
- Last-profitable-trade filters: strategy retains the filter flag; confirmed close fills and realized trade outcome determine its update event.

## Turtle state boundary

- `entry price`: the breakout/add trigger is strategy decision state; the actual initial/add fill price is execution state. Any subsequent 0.5N add or 2N stop anchor is mixed and must use confirmed fills.
- `pyramid`: the maximum-entry rule and next-add condition remain strategy-owned; confirmed entry count and filled quantity come from execution.
- `stop loss`: the 2N formula and channel-exit rule remain strategy-owned; order lifecycle and stop fill are execution-owned.
- `risk unit`: N, configured risk ratio, and desired unit calculation remain strategy decision state. Execution/account state supplies usable equity when the rule requires live equity and owns accepted/filled size.
- `position size`: requested size is strategy decision metadata; actual filled size and position are execution state.
- `pre_breakout_failure`: the last-profitable-trade filter remains strategy decision state, but its update must be driven by the realized result of a confirmed close.

Turtle therefore requires a pyramid fill-reconciliation adapter pattern; a signal-only adapter is insufficient. This is a design conclusion only and does not migrate Turtle.
