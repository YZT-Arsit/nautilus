#!/usr/bin/env python3
"""
A/B strategy comparison example.

A and B share the connector's cached data, but each strategy run receives a
fresh strategy instance and a fresh native BacktestEngine.
"""

from decimal import Decimal
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.runners import EngineRunConfig
from nautilus_ext.runners import NautilusStrategyComparisonRunner
from nautilus_ext.strategies import NautilusStrategySpec

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)
SYMBOL = "BCHUSDT"


class NoopStrategyA(Strategy):
    def __init__(self, bar_type):
        super().__init__()
        self.bar_type = bar_type

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        pass


class NoopStrategyB(Strategy):
    def __init__(self, bar_type):
        super().__init__()
        self.bar_type = bar_type

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        pass


if __name__ == "__main__":
    # This test-kit instrument only validates the adaptation and comparison chain.
    # Real Binance futures backtests should pass the matching internal Binance
    # futures Nautilus instrument.
    instrument = TestInstrumentProvider.eurusd_future(
        expiry_year=2024,
        expiry_month=3,
        venue_name="XCME",
    )

    connector = NautilusAutoBarDataConnector(
        root_path=ROOT,
        instrument=instrument,
        symbol=SYMBOL,
        max_files=1,
    )

    strategies = [
        NautilusStrategySpec(
            name="noop_a",
            factory=lambda ctx: NoopStrategyA(ctx.bar_type),
        ),
        NautilusStrategySpec(
            name="noop_b",
            factory=lambda ctx: NoopStrategyB(ctx.bar_type),
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

    comparison_runner = NautilusStrategyComparisonRunner(
        data_connector=connector,
        engine_config=engine_config,
        strategies=strategies,
        output_dir="internal_examples/output/strategy_ab_comparison",
    )

    results = comparison_runner.run_all()
    for result in results:
        print(f"run_id: {result.run_id}")
        print(f"strategy_name: {result.strategy_name}")
        print(f"bars_count: {result.bars_count}")
        print(f"output_dir: {result.output_dir}")
        result.engine.dispose()
