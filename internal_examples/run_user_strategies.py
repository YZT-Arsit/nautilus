#!/usr/bin/env python3
from decimal import Decimal
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from internal_examples.strategy_template import CountingStrategyTemplate
from internal_examples.strategy_template import StrategyTemplate

from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.instruments import AutoInstrumentBuilder
from nautilus_ext.runners import EngineRunConfig
from nautilus_ext.runners import NautilusMultiStrategyRunner
from nautilus_ext.strategies import NautilusStrategySpec

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
try:
    from nautilus_trader.model.currencies import USDT
except ImportError:
    USDT = USD


# =============================================================================
# User-editable area
# =============================================================================

DATA_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)
SYMBOL = "BCHUSDT"
VENUE = "BINANCE"
MAX_FILES = 1
OUTPUT_DIR = "outputs/user_strategies"
USE_TEST_INSTRUMENT_FALLBACK = False

USER_STRATEGIES = [
    NautilusStrategySpec(
        name="template_a",
        factory=lambda ctx: StrategyTemplate(ctx.bar_type, **ctx.params),
        params={"tag": "A"},
    ),
    NautilusStrategySpec(
        name="template_b",
        factory=lambda ctx: CountingStrategyTemplate(ctx.bar_type, **ctx.params),
        params={"tag": "B"},
    ),
]


# =============================================================================
# Usually no need to edit below this line
# =============================================================================

def build_instrument():
    # Default is a real auto instrument build. If you set
    # USE_TEST_INSTRUMENT_FALLBACK=True, it is only for interface-chain testing
    # and must not be used for production backtests.
    return AutoInstrumentBuilder.build(
        symbol=SYMBOL,
        data_root=DATA_ROOT,
        venue=VENUE,
        allow_test_fallback=USE_TEST_INSTRUMENT_FALLBACK,
    )


def build_engine_config():
    account_currency = USDT if USDT is not USD else USD  # TODO: prefer true USDT for Binance.
    return EngineRunConfig(
        venue=Venue(VENUE),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1_000_000, account_currency)],
        base_currency=account_currency,
        default_leverage=Decimal("1"),
        log_level="INFO",
    )


if __name__ == "__main__":
    connector = NautilusAutoBarDataConnector(
        root_path=DATA_ROOT,
        instrument=build_instrument(),
        symbol=SYMBOL,
        max_files=MAX_FILES,
    )
    runner = NautilusMultiStrategyRunner(
        data_connector=connector,
        engine_config=build_engine_config(),
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
