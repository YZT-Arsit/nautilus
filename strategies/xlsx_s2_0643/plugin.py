"""Normal StrategyPlugin registration seam for xlsx_s2_0643."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0643.config import XlsxS20643Config
from strategies.xlsx_s2_0643.strategy import XlsxS20643Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0643",
    config_cls=XlsxS20643Config,
    strategy_cls=XlsxS20643Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0643/config.yaml",
)
