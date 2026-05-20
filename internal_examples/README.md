# Nautilus Ext User Strategy Examples

This folder contains the stable user-facing entrypoints for the internal
Nautilus extension layer. Do not modify NautilusTrader official source code for
strategy experiments.

## Architecture

- `NautilusAutoBarDataConnector`: discovers CSV/Parquet bar data, infers schema
  and timeframe, and converts internal bars into Nautilus `Bar` objects.
- `NautilusStrategySpec` / `NautilusMultiStrategyRunner`: registers any number
  of strategy factories and runs each strategy independently with a fresh native
  Nautilus `BacktestEngine` and fresh strategy instance.
- `NautilusResultReporter` / `NautilusComparisonReporter`: writes one report
  directory per strategy and one multi-strategy comparison summary.

## Add A New Strategy

1. Copy `strategy_template.py` or add your own Nautilus native `Strategy` class.
2. Put the strategy logic mainly in `on_bar()`.
3. Register the strategy in `USER_STRATEGIES` inside `run_user_strategies.py`.
4. Run the user entrypoint.

Example command on Windows PowerShell:

```powershell
cd D:\nautilus
$env:PYTHONPATH="D:\nautilus"
python internal_examples\run_user_strategies.py
```

## Multi-Strategy Independent Comparison

To compare several strategies, add multiple `NautilusStrategySpec` entries to
`USER_STRATEGIES` in `run_user_strategies.py`. Each strategy receives:

- a fresh Nautilus `BacktestEngine`
- a fresh strategy instance created by its factory
- the same cached bars from the data connector

This is for horizontal comparison. It is not multiple strategies trading
together in the same account or the same engine.

## Strategy Switching Template

Use `strategy_switching_template.py` when you want one Nautilus Strategy to
switch internal logic by market regime. That template keeps switching logic
inside one strategy:

- `detect_regime()`
- `run_trend_logic()`
- `run_mean_reversion_logic()`
- `run_neutral_logic()`

This is different from `NautilusMultiStrategyRunner`, which runs independent
strategies separately for comparison.

## Volume Weighted Momentum Short Strategy

`nautilus_ext.strategies.volume_weighted_momentum_short` contains a Nautilus
Strategy migration of TradeBlazer `VolumeWeightedMomentumSys_S`.

Core logic:

- `momentum = close_t - close_{t - mom_len}`
- `VWM = EMA(volume * momentum, avg_len)`
- `BearSetup = VWM` crosses under zero
- `BullSetup = VWM` crosses over zero
- entry: after `BearSetup`, keep a short stop trigger valid for `setup_len`
  bars at `se_price - atr_pcnt * ATR`
- exit: after `BullSetup`, cover an existing short with a BUY market order

This strategy only consumes OHLCV `Bar` data. It should not be run directly on
TradeTick, QuoteTick, OrderBookDelta, OrderBookSnapshot, MarkPriceUpdate, or
FundingRateUpdate data. Those feeds must be aggregated into OHLCV bars first.
If bar volume is missing, always zero, or synthetic, the strategy semantics
change because volume is part of the signal.

The Nautilus implementation uses stop-market and market orders. This may not
match TradeBlazer's historical bar-internal fill model exactly, where entry is
expressed as `SellShort(..., Min(Open, trigger_price))`.

Example:

```powershell
python internal_examples\test_vwm_short_signal.py
python internal_examples\run_vwm_short_example.py
```

## Outputs

Reports are written under:

```text
outputs/user_strategies/
```

Each strategy receives its own run directory. The comparison reporter also
writes `comparison_summary.csv`, `comparison_summary.json`, and `README.md`.

## Instrument Note

The examples currently use a Nautilus test-kit instrument only to validate the
wrapper interface chain. Real Binance futures backtests should replace that
instrument with the company's internal Binance futures instrument builder or a
real Binance futures Nautilus instrument.

## Manual Instrument Configuration

The data connector still automatically identifies market data layout. Instrument
type selection is deliberately manual in production. In `run_user_strategies.py`
you must set:

```python
INSTRUMENT_TYPE = "crypto_perpetual"
VENUE = "BINANCE"
```

Other common examples:

```python
INSTRUMENT_TYPE = "currency_pair"
VENUE = "SIM"

INSTRUMENT_TYPE = "equity"
VENUE = "XNAS"

INSTRUMENT_TYPE = "futures_contract"
VENUE = "XCME"

INSTRUMENT_TYPE = "option_contract"
VENUE = "OPRA"
```

`INSTRUMENT_HINTS` fills gaps or overrides registry metadata:

```python
INSTRUMENT_HINTS = {
    "price_precision": 2,
    "size_precision": 3,
    "price_increment": "0.01",
    "size_increment": "0.001",
}
```

If the registry already contains `SYMBOL + VENUE + INSTRUMENT_TYPE`, you usually
only need to set those three values. If not, provide missing metadata such as
currency, precision, increments, expiry, strike, or underlying in
`INSTRUMENT_HINTS`.

`InstrumentTypeInferencer` still exists as an optional diagnostic/legacy helper,
but it is not used by the production default path. This avoids accidentally
misclassifying equity, FX, futures, options, or crypto instruments.

The unified instrument profile is designed for the main Nautilus instrument
families, including:

- `equity`
- `currency_pair`
- `commodity`
- `index`
- `futures_contract`
- `futures_spread`
- `crypto_future`
- `crypto_perpetual`
- `perpetual_contract`
- `option_contract`
- `option_spread`
- `crypto_option`
- `binary_option`
- `cfd`
- `betting`
- `synthetic`

For the current BDB/Futures/TLine/BinanceCryptoFutures data, set
`INSTRUMENT_TYPE = "crypto_perpetual"` and `VENUE = "BINANCE"`.

Production should use the company's official instrument metadata registry. The
test fallback is disabled by default and must not be used for real backtests.

If `run_user_strategies.py` fails because a Nautilus constructor adapter is not
complete yet, first verify profile and builder behavior separately:

```powershell
python internal_examples\test_manual_instrument_profile.py
python internal_examples\test_manual_instrument_required.py
python internal_examples\test_instrument_type_inference.py
python internal_examples\test_auto_instrument_profile.py
python internal_examples\test_auto_instrument_builder.py
```

## Engine Auto Config

`AutoEngineConfigBuilder` creates `EngineRunConfig` from the instrument profile.
It maps the venue string into Nautilus `Venue`, maps account/OMS strings into
Nautilus enums, and chooses account currency automatically:

1. explicit `ACCOUNT_CURRENCY`
2. `instrument_profile.settlement_currency`
3. `instrument_profile.quote_currency`
4. `USD` fallback with a warning

Users generally do not need to write `USDT`/`USD`, `Money`, `Venue`,
`AccountType`, or `OmsType` logic in `run_user_strategies.py`.

## Instrument Adapter and Registry Coverage

The instrument layer now uses one shared framework for all Nautilus instrument
families:

1. read explicit `INSTRUMENT_TYPE`, `VENUE`, and optional hints
2. look up a static or internal metadata registry
3. produce an `InstrumentProfile`
4. dispatch to a constructor adapter for the selected Nautilus instrument class

Current real-construction focus:

- `crypto_perpetual` / Nautilus `CryptoPerpetual`

The framework also includes profile, registry, requirements, and adapter
skeletons for:

- `currency_pair`
- `equity`
- `commodity`
- `index`
- `futures_contract`
- `futures_spread`
- `crypto_future`
- `perpetual_contract`
- `option_contract`
- `option_spread`
- `crypto_option`
- `binary_option`
- `cfd`
- `betting`
- `synthetic`

Supporting a type in the framework does not mean all real market metadata is
bundled in this repository. Production onboarding should:

1. connect the company's instrument metadata source
2. emit `InstrumentProfile` records
3. register them in `InstrumentRegistry`
4. complete or verify the constructor adapter for that instrument type

Use `INSTRUMENT_HINTS` in `run_user_strategies.py` when the registry is missing
fields, for example:

```python
INSTRUMENT_TYPE = "equity"
VENUE = "XNAS"
INSTRUMENT_HINTS = {
    "currency": "USD",
}
```

or:

```python
INSTRUMENT_TYPE = "option_contract"
VENUE = "OPRA"
INSTRUMENT_HINTS = {
    "underlying": "AAPL",
    "expiry": "20250117",
    "strike_price": "200",
    "option_kind": "CALL",
}
```

Do not use `TestInstrumentProvider` for real backtests.
