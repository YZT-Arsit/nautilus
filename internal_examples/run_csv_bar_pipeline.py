#!/usr/bin/env python3
"""
CSV bar pipeline example for the internal Nautilus extension layer.

This extension does not modify NautilusTrader source code. It only adapts
internal bar data into NautilusTrader data objects and then delegates execution
to the native BacktestEngine. Strategies must still be native NautilusTrader
Strategy instances, or callables which return one after the BarType is known.
"""

from decimal import Decimal
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.adapters import BarFieldMapping
from nautilus_ext.data_sources import CsvDataSource
from nautilus_ext.pipeline import NautilusBarBacktestPipeline
from nautilus_ext.runners import EngineRunConfig

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


class NoopStrategy(Strategy):
    def __init__(self, bar_type):
        super().__init__()
        self.bar_type = bar_type

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        pass


if __name__ == "__main__":
    # Template path only: replace this with a real internal CSV export.
    # Expected columns below are timestamp, open, high, low, close, volume.
    csv_file_path = "path/to/your_1min_bars.csv"

    instrument = TestInstrumentProvider.eurusd_future(
        expiry_year=2024,
        expiry_month=3,
        venue_name="XCME",
    )

    data_source = CsvDataSource(
        csv_file_path,
        sep=",",
    )
    field_mapping = BarFieldMapping(
        timestamp="timestamp",
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
    )

    # EngineRunConfig intentionally receives Nautilus native objects, not strings:
    # Venue(...), OmsType.NETTING, AccountType.MARGIN/CASH, Money(...).
    engine_config = EngineRunConfig(
        venue=Venue("XCME"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1_000_000, USD)],
        base_currency=USD,
        default_leverage=Decimal("1"),
        log_level="INFO",
    )

    # Passing a callable avoids a chicken-and-egg problem: the pipeline creates
    # the BarType during prepare_data(), then calls this factory with that BarType.
    pipeline = NautilusBarBacktestPipeline(
        data_source=data_source,
        field_mapping=field_mapping,
        instrument=instrument,
        timeframe="1-MINUTE",
        strategy=lambda bar_type: NoopStrategy(bar_type),
        engine_config=engine_config,
    )

    engine = pipeline.run()
    pipeline.export_results("internal_examples/output/csv_bar_pipeline")

    # The pipeline intentionally returns the native BacktestEngine so callers can
    # inspect reports, portfolio, cache, or dispose it when finished.
    engine.dispose()
