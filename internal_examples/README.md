# Nautilus Ext User Strategy Examples

This folder contains the stable user-facing entrypoint for internal backtests.
Do not modify NautilusTrader official source code for strategy experiments.

## Standard Workflow

1. Edit the User-editable area in `internal_examples/run_user_strategies.py`.
2. Fill `DATA_ROOT`, `SYMBOL`, `INSTRUMENT_TYPE`, `VENUE`, and optional
   `INSTRUMENT_HINTS`.
3. Edit strategy logic in `internal_examples/strategy_template.py`.
4. Register one or more strategies in `USER_STRATEGIES`.
5. Run:

```powershell
python internal_examples\run_user_strategies.py
```

`run_user_strategies.py` keeps the stable orchestration:

- `NautilusAutoBarDataConnector` reads and converts data to Nautilus bars.
- `AutoInstrumentProfileBuilder` / `AutoInstrumentBuilder` build the instrument
  from explicit user configuration and registry metadata.
- `AutoEngineConfigBuilder` builds the engine config.
- `NautilusMultiStrategyRunner` runs every registered strategy independently
  with a fresh engine and fresh strategy instance.

## User-Editable Fields

Users normally only change:

- `DATA_ROOT`
- `SYMBOL`
- `INSTRUMENT_TYPE`
- `VENUE`
- `ACCOUNT_CURRENCY`
- `INSTRUMENT_HINTS`
- `MAX_FILES`
- `OUTPUT_DIR`
- `USE_TEST_INSTRUMENT_FALLBACK`
- `STARTING_BALANCE`
- `USER_STRATEGIES`

Instrument type is deliberately explicit. Do not rely on production automatic
instrument type inference. Examples:

```python
INSTRUMENT_TYPE = "crypto_perpetual"
VENUE = "BINANCE"

INSTRUMENT_TYPE = "generic_futures"
VENUE = "CFFEX"
```

If registry metadata is incomplete, fill `INSTRUMENT_HINTS`, for example:

```python
INSTRUMENT_HINTS = {
    "price_precision": 1,
    "size_precision": 0,
    "price_increment": "0.2",
    "size_increment": "1",
    "currency": "CNY",
}
```

Ask for reliable instrument parameters before treating results as meaningful.
Do not hard-code uncertain contract metadata into production runs.

## Strategy Code

Single-strategy code lives in:

```text
internal_examples/strategy_template.py
```

The default `StrategyTemplate` is the TradeBlazer
`VolumeWeightedMomentumSys_S` short strategy migrated to NautilusTrader:

- `VWM = EMA(volume * Momentum(close, mom_len), avg_len)`
- `BearSetup = VWM` crosses under zero
- `BullSetup = VWM` crosses over zero
- on `BearSetup`, record `se_price = close` and `s_setup = 0`
- entry uses the previous bar's `se_price`, ATR, and `s_setup`
- short entry is valid within `setup_len`
- `BullSetup` covers an existing short

Register it from `run_user_strategies.py`:

```python
USER_STRATEGIES = [
    NautilusStrategySpec(
        name="vwm_short",
        factory=lambda ctx: StrategyTemplate(ctx.bar_type, **ctx.params),
        params={
            "strategy_kind": "vwm_short",
            "mom_len": 5,
            "avg_len": 20,
            "atr_len": 5,
            "atr_pcnt": 0.5,
            "setup_len": 5,
            "trade_size": 1,
        },
    ),
]
```

The strategy depends on OHLCV bars. If the source data is QuoteTick, OrderBook,
or another non-bar type, the connector or data preparation layer must convert it
to bars first. If `volume` is synthetic quote volume rather than traded volume,
the output is only suitable for engineering validation, not formal performance
conclusions.

## Strategy Switching

Use:

```text
internal_examples/strategy_switching_template.py
```

when one strategy instance should switch or combine internal logic by regime.
This is different from registering multiple `NautilusStrategySpec` entries,
which runs strategies independently for comparison.

## Outputs

The runner writes per-strategy report directories under `OUTPUT_DIR` and a
multi-strategy comparison summary. The main script prints:

- `run_id`
- `strategy_name`
- `bars_count`
- `report_dir`
- `metrics`
- `comparison_summary`

## Smoke Checks

```powershell
python -m py_compile internal_examples\run_user_strategies.py internal_examples\strategy_template.py internal_examples\strategy_switching_template.py
python internal_examples\test_strategy_template_vwm.py
```

Full Nautilus backtests also require compatible bar data and reliable
instrument metadata.
