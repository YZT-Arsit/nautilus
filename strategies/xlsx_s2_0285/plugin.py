"""Normal StrategyPlugin registration seam for xlsx_s2_0285."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0285.config import XlsxS20285Config
from strategies.xlsx_s2_0285.strategy import XlsxS20285Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0285",
    config_cls=XlsxS20285Config,
    strategy_cls=XlsxS20285Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0285/config.yaml",
)
