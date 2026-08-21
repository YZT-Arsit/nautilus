"""Normal StrategyPlugin registration seam for xlsx_s2_0315."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0315.config import XlsxS20315Config
from strategies.xlsx_s2_0315.strategy import XlsxS20315Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0315",
    config_cls=XlsxS20315Config,
    strategy_cls=XlsxS20315Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0315/config.yaml",
)
