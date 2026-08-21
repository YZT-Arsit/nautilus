"""Normal StrategyPlugin registration seam for xlsx_s2_0690."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0690.config import XlsxS20690Config
from strategies.xlsx_s2_0690.strategy import XlsxS20690Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0690",
    config_cls=XlsxS20690Config,
    strategy_cls=XlsxS20690Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0690/config.yaml",
)
