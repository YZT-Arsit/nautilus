from nautilus_ext.adapters.bar_adapter import BarDataAdapter
from nautilus_ext.builders.bar_builder import NautilusBarBuilder
from nautilus_ext.builders.bar_type_factory import BarTypeFactory
from nautilus_ext.builders.instrument_builder import InstrumentBuilder
from nautilus_ext.discovery.dataset_profile import DatasetProfile
from nautilus_ext.connectors.auto_bar_data_connector import NautilusAutoBarDataConnector
from nautilus_ext.reports.result_exporter import ResultExporter
from nautilus_ext.runners.backtest_runner import NautilusBacktestRunner
from nautilus_ext.runners.engine_runner import NautilusEngineRunner
from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec


class NautilusBarBacktestPipeline:
    def __init__(
        self,
        data_source,
        field_mapping,
        instrument,
        timeframe: str,
        strategy,
        engine_config,
        price_type: str = "LAST",
        source: str = "EXTERNAL",
        timestamp_unit: str | None = None,
        source_timezone: str | None = None,
    ):
        self.data_source = data_source
        self.field_mapping = field_mapping
        self.instrument = instrument
        self.timeframe = timeframe
        self.strategy = strategy
        self.engine_config = engine_config
        self.price_type = price_type
        self.source = source
        self.timestamp_unit = timestamp_unit
        self.source_timezone = source_timezone

        self.raw_df = None
        self.bar_df = None
        self.bar_type = None
        self.bars = None
        self.engine = None

    def prepare_data(self):
        self.raw_df = self.data_source.load()
        self.bar_df = BarDataAdapter(
            self.field_mapping,
            timestamp_unit=self.timestamp_unit,
            source_timezone=self.source_timezone,
        ).normalize(self.raw_df)
        self.instrument = InstrumentBuilder.require_existing_instrument(self.instrument)
        self.bar_type = BarTypeFactory.create(
            instrument=self.instrument,
            timeframe=self.timeframe,
            price_type=self.price_type,
            source=self.source,
        )
        self.bars = NautilusBarBuilder(self.instrument, self.bar_type).build(self.bar_df)
        return self.bars

    def run(self):
        if self.bars is None or self.bar_type is None:
            self.prepare_data()

        strategy = self._build_strategy()
        self.engine = NautilusEngineRunner(self.engine_config).run(
            instrument=self.instrument,
            data=self.bars,
            strategy=strategy,
        )
        return self.engine

    def export_results(self, output_dir: str):
        if self.engine is None:
            raise ValueError("Cannot export results before running the pipeline.")

        return ResultExporter(self.engine).export_placeholder(output_dir)

    def _build_strategy(self):
        if self.bar_type is None:
            raise ValueError("Cannot build strategy before bar_type has been prepared.")

        if callable(self.strategy):
            return self.strategy(self.bar_type)

        return self.strategy


class NautilusAutoBarPipeline:
    def __init__(
        self,
        root_path: str,
        instrument,
        strategy,
        engine_config,
        symbol: str | None = None,
        start: str | None = None,
        end: str | None = None,
        max_files: int | None = None,
    ):
        self.root_path = root_path
        self.instrument = instrument
        self.strategy = strategy
        self.engine_config = engine_config
        self.symbol = symbol
        self.start = start
        self.end = end
        self.max_files = max_files
        self.connector = NautilusAutoBarDataConnector(
            root_path=root_path,
            instrument=instrument,
            symbol=symbol,
            start=start,
            end=end,
            max_files=max_files,
        )

        self.profile = None
        self.raw_df = None
        self.bar_df = None
        self.bar_type = None
        self.bars = None
        self.engine = None

    def discover(self) -> DatasetProfile:
        self.profile = self.connector.discover()
        return self.profile

    def load_raw_data(self):
        self.raw_df = self.connector.load_raw_data()
        return self.raw_df

    def prepare_data(self):
        self.bars = self.connector.prepare_data()
        self.profile = self.connector.profile
        self.raw_df = self.connector.raw_df
        self.bar_df = self.connector.bar_df
        self.bar_type = self.connector.bar_type
        self.instrument = self.connector.instrument
        return self.bars

    def run(self):
        if isinstance(self.strategy, NautilusStrategySpec):
            runner = NautilusBacktestRunner(
                data_connector=self.connector,
                engine_config=self.engine_config,
            )
            result = runner.run_strategy(self.strategy)
            self.profile = self.connector.profile
            self.raw_df = self.connector.raw_df
            self.bar_df = self.connector.bar_df
            self.bar_type = self.connector.bar_type
            self.bars = self.connector.bars
            self.engine = result.engine
            return self.engine

        self.prepare_data()
        strategy = self._build_strategy()
        self.engine = NautilusEngineRunner(self.engine_config).run(
            instrument=self.instrument,
            data=self.bars,
            strategy=strategy,
        )
        return self.engine

    def _build_strategy(self):
        if self.bar_type is None:
            raise ValueError("Cannot build strategy before bar_type has been prepared.")

        if callable(self.strategy):
            return self.strategy(self.bar_type)

        return self.strategy
