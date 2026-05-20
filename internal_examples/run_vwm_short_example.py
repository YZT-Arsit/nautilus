"""Example registration for the VWM short strategy.

This example shows how to plug the TradeBlazer
``VolumeWeightedMomentumSys_S`` migration into the internal multi-strategy
runner. The strategy itself is bar-only and requires OHLCV bars with meaningful
volume. TradeTick, QuoteTick, OrderBook, MarkPrice, or FundingRate data must be
aggregated to OHLCV bars before this strategy can be used.
"""

from decimal import Decimal
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from internal_examples.run_user_strategies import DATA_ROOT
from internal_examples.run_user_strategies import INSTRUMENT_HINTS
from internal_examples.run_user_strategies import INSTRUMENT_TYPE
from internal_examples.run_user_strategies import MAX_FILES
from internal_examples.run_user_strategies import OUTPUT_DIR
from internal_examples.run_user_strategies import SYMBOL
from internal_examples.run_user_strategies import USE_TEST_INSTRUMENT_FALLBACK
from internal_examples.run_user_strategies import VENUE
from internal_examples.run_user_strategies import build_engine_config
from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.instruments import AutoInstrumentBuilder
from nautilus_ext.instruments import AutoInstrumentProfileBuilder
from nautilus_ext.runners import NautilusMultiStrategyRunner
from nautilus_ext.strategies import NautilusStrategySpec
from nautilus_ext.strategies import StrategyContext
from nautilus_ext.strategies.volume_weighted_momentum_short import (
    VolumeWeightedMomentumShortConfig,
)
from nautilus_ext.strategies.volume_weighted_momentum_short import (
    VolumeWeightedMomentumShortStrategy,
)


def build_vwm_short_strategy(ctx: StrategyContext):
    return VolumeWeightedMomentumShortStrategy(
        VolumeWeightedMomentumShortConfig(
            instrument_id=ctx.instrument.id,
            bar_type=ctx.bar_type,
            trade_size=Decimal(str(ctx.params.get("trade_size", "1"))),
            mom_len=int(ctx.params.get("mom_len", 5)),
            avg_len=int(ctx.params.get("avg_len", 20)),
            atr_len=int(ctx.params.get("atr_len", 5)),
            atr_pcnt=Decimal(str(ctx.params.get("atr_pcnt", "0.5"))),
            setup_len=int(ctx.params.get("setup_len", 5)),
            emulation_trigger=str(ctx.params.get("emulation_trigger", "NO_TRIGGER")),
        ),
    )


def main():
    profile = AutoInstrumentProfileBuilder.build_profile(
        symbol=SYMBOL,
        data_root=DATA_ROOT,
        instrument_type=INSTRUMENT_TYPE,
        venue=VENUE,
        hints=INSTRUMENT_HINTS,
        require_explicit_type=True,
    )
    instrument = AutoInstrumentBuilder.build(
        symbol=SYMBOL,
        data_root=DATA_ROOT,
        instrument_type=INSTRUMENT_TYPE,
        venue=VENUE,
        hints=INSTRUMENT_HINTS,
        allow_test_fallback=USE_TEST_INSTRUMENT_FALLBACK,
        require_explicit_type=True,
    )
    engine_config = build_engine_config(profile)
    connector = NautilusAutoBarDataConnector(
        root_path=DATA_ROOT,
        instrument=instrument,
        symbol=SYMBOL,
        max_files=MAX_FILES,
    )

    runner = NautilusMultiStrategyRunner(
        data_connector=connector,
        engine_config=engine_config,
        strategies=[
            NautilusStrategySpec(
                name="vwm_short",
                factory=build_vwm_short_strategy,
                params={
                    "trade_size": "1",
                    "mom_len": 5,
                    "avg_len": 20,
                    "atr_len": 5,
                    "atr_pcnt": "0.5",
                    "setup_len": 5,
                },
            ),
        ],
        output_dir=OUTPUT_DIR,
    )

    for result in runner.run_all():
        print(f"run_id: {result.run_id}")
        print(f"strategy_name: {result.strategy_name}")
        print(f"bars_count: {result.bars_count}")
        print(f"report_dir: {result.report_dir}")


if __name__ == "__main__":
    main()
