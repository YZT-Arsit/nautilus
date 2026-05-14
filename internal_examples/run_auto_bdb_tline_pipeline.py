#!/usr/bin/env python3
"""
Auto-discovery example for BDB TLine bar data.

This extension layer does not modify NautilusTrader source code. It only
discovers internal dataset shape, adapts bar data into NautilusTrader data
objects, and runs the native BacktestEngine. Strategies must still be native
NautilusTrader Strategy instances, or callables returning one after BarType is
known.
"""

from decimal import Decimal
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.pipeline import NautilusAutoBarPipeline
from nautilus_ext.reports import ResultExporter
from nautilus_ext.runners import EngineRunConfig

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


ROOT_PATH = (
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
    # This instrument is only for validating the automatic data-adaptation chain.
    # Production use should pass the matching Nautilus instrument from an internal
    # instrument factory.
    instrument = TestInstrumentProvider.btcusdt_binance()

    engine_config = EngineRunConfig(
        venue=Venue("BINANCE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1_000_000, USD)],
        base_currency=USD,
        default_leverage=Decimal("1"),
        log_level="INFO",
    )

    pipeline = NautilusAutoBarPipeline(
        root_path=ROOT_PATH,
        instrument=instrument,
        strategy=lambda bar_type: NoopStrategy(bar_type),
        engine_config=engine_config,
        symbol=SYMBOL,
        max_files=1,
    )

    profile = pipeline.discover()
    print(f"profile: {profile}")

    engine = pipeline.run()
    output_path = ResultExporter(engine).export_placeholder(
        "internal_examples/output/auto_bdb_tline_pipeline",
    )

    print(f"bar_type: {pipeline.bar_type}")
    print(f"bars: {len(pipeline.bars)}")
    print(f"run_info: {output_path}")

    engine.dispose()
