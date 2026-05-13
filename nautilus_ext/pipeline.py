from nautilus_ext.adapters.bar_adapter import BarDataAdapter
from nautilus_ext.builders.bar_builder import NautilusBarBuilder
from nautilus_ext.builders.bar_type_factory import BarTypeFactory
from nautilus_ext.builders.instrument_builder import InstrumentBuilder
from nautilus_ext.reports.result_exporter import ResultExporter
from nautilus_ext.runners.engine_runner import NautilusEngineRunner


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
    ):
        self.data_source = data_source
        self.field_mapping = field_mapping
        self.instrument = instrument
        self.timeframe = timeframe
        self.strategy = strategy
        self.engine_config = engine_config
        self.price_type = price_type
        self.source = source

        self.raw_df = None
        self.bar_df = None
        self.bar_type = None
        self.bars = None
        self.engine = None

    def prepare_data(self):
        self.raw_df = self.data_source.load()
        self.bar_df = BarDataAdapter(self.field_mapping).normalize(self.raw_df)
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
        if self.bars is None:
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
