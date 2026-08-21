"""Normal StrategyPlugin registration seam for xlsx_s2_0665."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0665.config import XlsxS20665Config
from strategies.xlsx_s2_0665.strategy import XlsxS20665Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0665",
    config_cls=XlsxS20665Config,
    strategy_cls=XlsxS20665Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0665/config.yaml",
)
