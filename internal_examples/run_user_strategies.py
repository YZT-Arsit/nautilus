#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from internal_examples.strategy_template import StrategyTemplate

from nautilus_ext.config import AutoEngineConfigBuilder
from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.instruments import AutoInstrumentBuilder
from nautilus_ext.instruments import AutoInstrumentProfileBuilder
from nautilus_ext.runners import NautilusMultiStrategyRunner
from nautilus_ext.strategies import NautilusStrategySpec

# =============================================================================
# User-editable area
# =============================================================================

DATA_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)
SYMBOL = "BCHUSDT"

# Required manual instrument configuration.
# Do not rely on automatic instrument type inference in production.
INSTRUMENT_TYPE = "crypto_perpetual"
VENUE = "BINANCE"

# Optional. If None, AutoEngineConfigBuilder may infer from instrument profile.
ACCOUNT_CURRENCY = None

# Optional metadata override. Use this when registry metadata is incomplete.
INSTRUMENT_HINTS = {
    # Examples:
    # "base_currency": "BCH",
    # "quote_currency": "USDT",
    # "settlement_currency": "USDT",
    # "price_precision": 2,
    # "size_precision": 3,
    # "price_increment": "0.01",
    # "size_increment": "0.001",
}

MAX_FILES = 1
OUTPUT_DIR = "outputs/user_strategies"
USE_TEST_INSTRUMENT_FALLBACK = False
STARTING_BALANCE = 1_000_000

USER_STRATEGIES = [
    NautilusStrategySpec(
        name="template_a",
        factory=lambda ctx: StrategyTemplate(ctx.bar_type, **ctx.params),
        params={"tag": "A"},
    ),
]

# =============================================================================
# No need to edit below this line
# =============================================================================

def build_instrument_profile():
    return AutoInstrumentProfileBuilder.build_profile(
        symbol=SYMBOL,
        data_root=DATA_ROOT,
        instrument_type=INSTRUMENT_TYPE,
        venue=VENUE,
        hints=INSTRUMENT_HINTS,
        require_explicit_type=True,
    )

def build_instrument():
    return AutoInstrumentBuilder.build(
        symbol=SYMBOL,
        data_root=DATA_ROOT,
        instrument_type=INSTRUMENT_TYPE,
        venue=VENUE,
        hints=INSTRUMENT_HINTS,
        allow_test_fallback=USE_TEST_INSTRUMENT_FALLBACK,
        require_explicit_type=True,
    )

def build_engine_config(instrument_profile):
    return AutoEngineConfigBuilder.build(
        venue=instrument_profile.venue,
        instrument_profile=instrument_profile,
        starting_balance=STARTING_BALANCE,
        account_currency=ACCOUNT_CURRENCY,
        log_level="INFO",
    )

if __name__ == "__main__":
    instrument_profile = build_instrument_profile()
    print(f"instrument_type: {instrument_profile.instrument_type}")
    print(f"venue: {instrument_profile.venue}")
    print(f"instrument_id: {instrument_profile.instrument_id}")
    print(f"source: {instrument_profile.source}")
    print(f"settlement_currency: {instrument_profile.settlement_currency}")
    if instrument_profile.source == "manual_partial":
        print(
            "Instrument profile is partial; constructor may fail unless "
            "INSTRUMENT_HINTS provides required metadata."
        )

    connector = NautilusAutoBarDataConnector(
        root_path=DATA_ROOT,
        instrument=build_instrument(),
        symbol=SYMBOL,
        max_files=MAX_FILES,
    )
    runner = NautilusMultiStrategyRunner(
        data_connector=connector,
        engine_config=build_engine_config(instrument_profile),
        strategies=USER_STRATEGIES,
        output_dir=OUTPUT_DIR,
    )

    results = runner.run_all()
    for result in results:
        print(f"run_id: {result.run_id}")
        print(f"strategy_name: {result.strategy_name}")
        print(f"bars_count: {result.bars_count}")
        print(f"report_dir: {result.report_dir}")
        print(f"metrics: {result.metrics}")
        if result.engine is not None:
            result.engine.dispose()

    print(f"comparison_summary: {runner.comparison_report_files}")
