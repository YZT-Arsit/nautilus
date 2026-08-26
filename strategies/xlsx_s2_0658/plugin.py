"""Normal StrategyPlugin registration seam for xlsx_s2_0658."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0658.config import XlsxS20658Config
from strategies.xlsx_s2_0658.strategy import XlsxS20658Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0658",
    config_cls=XlsxS20658Config,
    strategy_cls=XlsxS20658Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0658/config.yaml",
)
