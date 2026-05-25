"""Run VWM short on OHLCV bars generated from real IH2303 QuoteTick data.

The generated bars use synthetic tick-count volume. This script validates the
engineering path only; its results are not formal strategy performance results.
"""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from internal_examples.strategy_template import StrategyTemplate

from nautilus_ext.config import AutoEngineConfigBuilder
from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.runners import NautilusMultiStrategyRunner
from nautilus_ext.strategies import NautilusStrategySpec
from nautilus_trader.persistence.catalog import ParquetDataCatalog


CATALOG_PATH = Path(r"D:\QuanHub\DataHome\DataTrans\nautilus_catalog")
GENERATED_BARS_PATH = Path(r"D:\nautilus\outputs\generated_bars")
INSTRUMENT_ID = "IH2303.CFFEX"
OUTPUT_DIR = r"D:\nautilus\outputs\vwm_generated_bars"


def load_real_instrument():
    attempted = []
    for path in [CATALOG_PATH / "cffex_l1_quote", CATALOG_PATH]:
        attempted.append(str(path))
        catalog = ParquetDataCatalog(str(path))
        instruments = catalog.instruments(instrument_ids=[INSTRUMENT_ID])
        if instruments:
            return instruments[0]

    raise ValueError(
        f"No catalog instrument metadata found for {INSTRUMENT_ID}. "
        f"Attempted catalog paths: {attempted}"
    )


def main() -> None:
    print("WARNING: Generated bars use synthetic tick_count volume, not traded volume.")
    print("WARNING: This backtest is for engineering validation only.")
    instrument = load_real_instrument()
    connector = NautilusAutoBarDataConnector(
        root_path=str(GENERATED_BARS_PATH),
        instrument=instrument,
        symbol=INSTRUMENT_ID,
        max_files=1,
    )
    engine_config = AutoEngineConfigBuilder.build(
        venue="CFFEX",
        starting_balance=1_000_000,
        account_currency="CNY",
        log_level="INFO",
    )
    strategies = [
        NautilusStrategySpec(
            name="vwm_short_generated_bars",
            factory=lambda ctx: StrategyTemplate(ctx.bar_type, **ctx.params),
            params={
                "strategy_kind": "vwm_short",
                "mom_len": 5,
                "avg_len": 20,
                "atr_len": 5,
                "atr_pcnt": 0.5,
                "setup_len": 5,
                "trade_size": 1,
            },
        ),
    ]
    runner = NautilusMultiStrategyRunner(
        data_connector=connector,
        engine_config=engine_config,
        strategies=strategies,
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


if __name__ == "__main__":
    main()
