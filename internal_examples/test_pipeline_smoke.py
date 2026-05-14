#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.runners import NautilusMultiStrategyRunner
from nautilus_ext.runners import NautilusStrategyComparisonRunner
from nautilus_ext.strategies import NautilusStrategySpec
from nautilus_ext.strategies import StrategyContext


class DummyConnector:
    pass


def factory(ctx):
    return object()


def expect_value_error(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


assert NautilusAutoBarDataConnector is not None
assert NautilusStrategyComparisonRunner is NautilusMultiStrategyRunner

ctx = StrategyContext(
    bar_type="bar_type",
    instrument="instrument",
    strategy_name="smoke",
    run_id="smoke_001",
    params={},
)
spec = NautilusStrategySpec.from_callable("smoke", factory)
assert spec.build_strategy(ctx) is not spec.build_strategy(ctx)

expect_value_error(
    lambda: NautilusMultiStrategyRunner(
        data_connector=DummyConnector(),
        engine_config=object(),
        strategies=[],
    )
)
expect_value_error(
    lambda: NautilusMultiStrategyRunner(
        data_connector=DummyConnector(),
        engine_config=object(),
        strategies=[
            NautilusStrategySpec("dup", factory),
            NautilusStrategySpec("dup", factory),
        ],
    )
)

runner = NautilusMultiStrategyRunner(
    data_connector=DummyConnector(),
    engine_config=object(),
    strategies=[
        NautilusStrategySpec("enabled", factory),
        NautilusStrategySpec("disabled", factory, enabled=False),
    ],
)
assert len(runner._enabled_strategies()) == 1

print("pipeline smoke ok")
