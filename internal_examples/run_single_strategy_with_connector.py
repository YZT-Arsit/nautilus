#!/usr/bin/env python3
"""
Single-strategy example using the data connector and backtest runner.

The connector owns data discovery and bar conversion. The runner owns fresh
strategy construction and fresh BacktestEngine execution.
"""

from decimal import Decimal
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.runners import EngineRunConfig
from nautilus_ext.runners import NautilusBacktestRunner
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


class NoopStrategy(Strategy):
    def __init__(self, bar_type):
        super().__init__()
        self.bar_type = bar_type

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        pass


if __name__ == "__main__":
    # This test-kit instrument only validates the adaptation and runner chain.
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

    strategy_spec = NautilusStrategySpec(
        name="noop_single",
        factory=lambda ctx: NoopStrategy(ctx.bar_type),
    )

    engine_config = EngineRunConfig(
        venue=Venue("XCME"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1_000_000, USD)],
        base_currency=USD,
        default_leverage=Decimal("1"),
        log_level="INFO",
    )

    runner = NautilusBacktestRunner(
        data_connector=connector,
        engine_config=engine_config,
        output_dir="internal_examples/output/single_strategy_with_connector",
    )
    result = runner.run_strategy(strategy_spec)

    print(f"run_id: {result.run_id}")
    print(f"strategy_name: {result.strategy_name}")
    print(f"bars_count: {result.bars_count}")
    print(f"bar_type: {result.bar_type}")
    print(f"output_dir: {result.output_dir}")

    result.engine.dispose()
