import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from nautilus_ext.results.result_reporter import NautilusResultReporter
from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec
from nautilus_ext.strategies.strategy_spec import StrategyContext

log = logging.getLogger(__name__)


@dataclass
class BacktestRunResult:
    run_id: str
    strategy_name: str
    engine: object
    bar_type: object
    bars_count: int
    output_dir: str | None = None
    report_dir: str | None = None
    report_files: dict | None = None
    metrics: dict | None = None
    status: str = "completed"
    error: str | None = None


class NautilusBacktestRunner:
    def __init__(
        self,
        data_connector,
        engine_config,
        output_dir: str | None = None,
    ):
        self.data_connector = data_connector
        self.engine_config = engine_config
        self.output_dir = output_dir

    def run_strategy(self, strategy_spec: NautilusStrategySpec, feature_pipeline=None):
        """Run a backtest strategy with optional offline feature generation.

        Parameters
        ----------
        strategy_spec : NautilusStrategySpec
        feature_pipeline : FeaturePipeline | None
            If provided, converts all backtest bars to BarInput and runs them
            through the pipeline before starting the backtest engine.  Features
            are flushed to Parquet under output_dir/features/.
            This is the offline feature generation path; it does NOT integrate
            with the Nautilus DataEngine event loop (reserved for future work).
        """
        if not strategy_spec.enabled:
            raise ValueError(f"Cannot run disabled strategy spec {strategy_spec.name!r}.")

        bars = self.data_connector.prepare_data()
        bar_type = self.data_connector.get_bar_type()

        if feature_pipeline is not None:
            features_dir = (
                Path(self.output_dir) / "features" if self.output_dir else None
            )
            n = self._run_feature_pipeline(bars, feature_pipeline, features_dir)
            log.info("BacktestRunner: offline feature generation wrote %d rows", n)

        run_id = self._make_run_id(strategy_spec.name)
        context = StrategyContext(
            bar_type=bar_type,
            instrument=self.data_connector.instrument,
            strategy_name=strategy_spec.name,
            run_id=run_id,
            params=dict(strategy_spec.params or {}),
        )
        strategy = strategy_spec.build_strategy(context)

        from nautilus_ext.runners.engine_runner import NautilusEngineRunner

        engine = NautilusEngineRunner(self.engine_config).run(
            instrument=self.data_connector.instrument,
            data=bars,
            strategy=strategy,
        )

        run_output_dir = None
        report_files = None
        metrics = {"available": False}
        if self.output_dir is not None:
            run_output_dir = str(Path(self.output_dir) / run_id)
            result = BacktestRunResult(
                run_id=run_id,
                strategy_name=strategy_spec.name,
                engine=engine,
                bar_type=bar_type,
                bars_count=len(bars),
                output_dir=self.output_dir,
                report_dir=run_output_dir,
                metrics=metrics,
            )
            report_files = NautilusResultReporter(result).export(run_output_dir)
            result.report_files = report_files
            return result

        return BacktestRunResult(
            run_id=run_id,
            strategy_name=strategy_spec.name,
            engine=engine,
            bar_type=bar_type,
            bars_count=len(bars),
            output_dir=self.output_dir,
            report_dir=run_output_dir,
            report_files=report_files,
            metrics=metrics,
        )

    def _run_feature_pipeline(self, bars, feature_pipeline, features_dir=None) -> int:
        """Convert Nautilus Bar objects to BarInput and compute offline features.

        Returns the number of feature rows flushed to Parquet.

        Nautilus Bar attributes used:
          bar.open / .high / .low / .close / .volume  — Price / Quantity objects
          bar.ts_event                                 — nanoseconds since epoch
          bar.bar_type.instrument_id                  — InstrumentId object
          bar.bar_type                                — BarType object
        """
        try:
            from nautilus_ext.strategies.interfaces.input_types import BarInput
        except ImportError:
            log.warning("BarInput not importable; skipping feature pipeline.")
            return 0

        bar_inputs = []
        for bar in bars:
            try:
                bar_inputs.append(BarInput(
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(bar.volume),
                    ts_event=int(bar.ts_event) // 1_000_000,  # ns → ms
                    instrument_id=str(bar.bar_type.instrument_id),
                    bar_type=str(bar.bar_type),
                ))
            except Exception as exc:
                log.debug("Bar conversion skipped: %s", exc)

        if not bar_inputs:
            log.warning("BacktestRunner: no bars could be converted to BarInput.")
            return 0

        if features_dir is not None:
            offline = getattr(feature_pipeline, "_offline_store", None)
            if offline is None:
                from feature_engine.feature_store import OfflineFeatureStore
                offline = OfflineFeatureStore(features_dir)
                feature_pipeline._offline_store = offline

        feature_pipeline.update_many(bar_inputs)
        n = feature_pipeline.flush()

        if features_dir is not None:
            offline = getattr(feature_pipeline, "_offline_store", None)
            if offline is not None:
                for engine in getattr(feature_pipeline, "engines", []):
                    try:
                        offline.write_schema(engine.schema)
                    except Exception:
                        pass
        return n

    @staticmethod
    def _make_run_id(strategy_name: str) -> str:
        safe_name = "".join(
            char.lower() if char.isalnum() else "_"
            for char in strategy_name.strip()
        ).strip("_")
        if not safe_name:
            safe_name = "strategy"

        timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
        return f"{safe_name}_{timestamp}_{uuid4().hex[:8]}"
