#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.config import AutoEngineConfigBuilder
from nautilus_ext.connectors import NautilusAutoBarDataConnector
from nautilus_ext.instruments import AutoInstrumentProfileBuilder
from nautilus_ext.runners import NautilusMultiStrategyRunner
from nautilus_ext.runners import NautilusStrategyComparisonRunner
from nautilus_ext.strategies import NautilusStrategySpec
from nautilus_ext.strategies import StrategyContext


class DummyConnector:
    pass


def factory(ctx):
    return object()


assert AutoEngineConfigBuilder is not None
assert NautilusAutoBarDataConnector is not None
assert AutoInstrumentProfileBuilder is not None
assert NautilusStrategyComparisonRunner is NautilusMultiStrategyRunner

ctx = StrategyContext("bar_type", "instrument", "smoke", "run_001", {})
spec = NautilusStrategySpec.from_callable("smoke", factory)
assert spec.build_strategy(ctx) is not spec.build_strategy(ctx)

try:
    NautilusMultiStrategyRunner(DummyConnector(), object(), [])
except ValueError:
    pass
else:
    raise AssertionError("Expected empty strategies to fail")

print("pipeline smoke ok")
