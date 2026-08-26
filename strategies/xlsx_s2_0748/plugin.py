"""Normal StrategyPlugin registration seam for xlsx_s2_0748."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0748.config import XlsxS20748Config
from strategies.xlsx_s2_0748.strategy import XlsxS20748Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0748",
    config_cls=XlsxS20748Config,
    strategy_cls=XlsxS20748Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0748/config.yaml",
)
