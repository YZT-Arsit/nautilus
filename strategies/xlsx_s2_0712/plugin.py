"""Normal StrategyPlugin registration seam for xlsx_s2_0712."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0712.config import XlsxS20712Config
from strategies.xlsx_s2_0712.strategy import XlsxS20712Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0712",
    config_cls=XlsxS20712Config,
    strategy_cls=XlsxS20712Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0712/config.yaml",
)
