#!/usr/bin/env python3
"""
Formal N-strategy comparison example.

This is multi-strategy independent backtesting and horizontal comparison. It is
not a same-engine portfolio run with multiple strategies trading together.
"""

from decimal import Decimal
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from internal_examples.strategy_template import CountingStrategyTemplate
from internal_examples.strategy_template import StrategyTemplate

from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.runners import EngineRunConfig
from nautilus_ext.runners import NautilusMultiStrategyRunner
from nautilus_ext.strategies import NautilusStrategySpec

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider


DATA_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)
SYMBOL = "BCHUSDT"


if __name__ == "__main__":
    # This test-kit instrument only validates the wrapper interface chain.
    # Real Binance futures backtests should replace this with an internal
    # Binance futures Nautilus instrument.
    instrument = TestInstrumentProvider.eurusd_future(
        expiry_year=2024,
        expiry_month=3,
        venue_name="XCME",
    )

    connector = NautilusAutoBarDataConnector(
        root_path=DATA_ROOT,
        instrument=instrument,
        symbol=SYMBOL,
        max_files=1,
    )
    strategies = [
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
        NautilusStrategySpec(
            name="template_c",
            factory=lambda ctx: StrategyTemplate(ctx.bar_type, **ctx.params),
            params={"tag": "C"},
        ),
    ]
    engine_config = EngineRunConfig(
        venue=Venue("XCME"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1_000_000, USD)],
        base_currency=USD,
        default_leverage=Decimal("1"),
        log_level="INFO",
    )
    runner = NautilusMultiStrategyRunner(
        data_connector=connector,
        engine_config=engine_config,
        strategies=strategies,
        output_dir="outputs/multi_strategy_comparison",
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
