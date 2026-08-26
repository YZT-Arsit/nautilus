"""Normal StrategyPlugin registration seam for xlsx_s2_0745."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0745.config import XlsxS20745Config
from strategies.xlsx_s2_0745.strategy import XlsxS20745Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0745",
    config_cls=XlsxS20745Config,
    strategy_cls=XlsxS20745Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0745/config.yaml",
)
