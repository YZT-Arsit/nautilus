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

## Instrument Auto Builder

The data connector automatically identifies market data layout. The instrument
auto layer separately infers the Nautilus instrument profile from `DATA_ROOT`,
`SYMBOL`, explicit hints, and static metadata registries.

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

For the current BDB/Futures/TLine/BinanceCryptoFutures data, `BCHUSDT` should
infer as `crypto_perpetual` on venue `BINANCE`.

Production should use the company's official instrument metadata registry. The
test fallback is disabled by default and must not be used for real backtests.

If `run_user_strategies.py` fails because a Nautilus constructor adapter is not
complete yet, first verify inference separately:

```powershell
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
