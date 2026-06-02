# Nautilus Ext Strategy Interface

`nautilus_ext` separates strategy selection from market data and runner code.
The goal is that an entry file changes configuration, not strategy internals.

## Why this interface exists

The first internal strategy, `vwm_short`, is an OHLCV Bar strategy. Future
strategies may consume trade ticks, quote ticks, order books, funding rates,
feature vectors, multiple symbols, or multiple timeframes. A fixed strategy
interface keeps those variants from leaking into `StrategyTemplate`,
`BaseBarStrategy`, or ccxt live runners.

## Core types

Strategy inputs live in `nautilus_ext.strategies.interfaces.input_types`:

- `BarInput`
- `TradeTickInput`
- `QuoteTickInput`
- `OrderBookInput`
- `FundingRateInput`
- `FeatureVectorInput`

Strategy outputs live in `nautilus_ext.strategies.interfaces.output_types`:

- `OrderIntent`
- `SignalResult`

`SignalResult` keeps legacy fields such as `entry_side`, `entry_order_type`,
`entry_price`, `exit_side`, and `cancel_entry`. It also exposes the new
`order_intents` list for future non-Bar strategies and multi-order outputs.

## Developing a Bar strategy

Implement a pure signal engine which accepts `BarInput` and returns
`SignalResult`.

```python
from nautilus_ext.strategies.signal_types import BarInput, SignalResult


class MyBarSignalEngine:
    name = "my_bar_strategy"

    def reset(self):
        ...

    def update(self, event: BarInput, context: dict | None = None):
        return SignalResult(signal_name=self.name, order_intents=[])
```

Register it once:

```python
from nautilus_ext.strategies.registry import register_signal_engine


@register_signal_engine("my_bar_strategy")
class MyBarSignalEngine:
    ...
```

## Non-Bar strategies

Declare the desired inputs in `StrategyInputSchema`:

```json
{
  "input_types": ["quote_tick"],
  "symbols": ["BTC/USDT:USDT"],
  "requires_position": true
}
```

The current ccxt polling runner only emits Bars. QuoteTick, OrderBook,
FundingRate, multi-asset, and multi-timeframe feeds are interface-ready but
still need concrete feed implementations.

## Switching strategies by config

Use `StrategySpecV2` JSON:

```json
{
  "name": "vwm_short",
  "input_schema": {
    "input_types": ["bar"],
    "symbols": ["BTC/USDT:USDT"],
    "timeframes": ["1m"],
    "warmup": 200
  },
  "params": {
    "mom_len": 5,
    "avg_len": 20,
    "atr_len": 5,
    "atr_pcnt": 0.5,
    "setup_len": 5
  },
  "execution": {
    "trade_size": 1,
    "mode": "dry_run"
  }
}
```

Then call:

```python
from nautilus_ext.strategies.registry import build_signal_engine

signal_engine = build_signal_engine(strategy_spec_dict)
```

`CcxtPaperLiveRunner(config, strategy_spec_dict)` also accepts this format
directly.

## Recording debug

Put strategy-specific fields in `SignalResult.debug`. `SignalRecorder` preserves
known Bar columns and writes all debug/state data to `debug_json` and
`state_json`, so new strategies do not need recorder schema changes for every
feature.

## Current limits

- `BaseBarStrategy` remains Bar-only.
- `CcxtPollingBarFeed` emits OHLCV bars only.
- Non-Bar feed interfaces are present, but concrete tick/orderbook/funding
  adapters are future work.
- Real order submission is not implemented in ccxt paper live; it records
  dry-run intents only.
