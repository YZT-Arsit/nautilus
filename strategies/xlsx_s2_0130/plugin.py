"""Normal StrategyPlugin registration seam for xlsx_s2_0130."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0130.config import XlsxS20130Config
from strategies.xlsx_s2_0130.strategy import XlsxS20130Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0130",
    config_cls=XlsxS20130Config,
    strategy_cls=XlsxS20130Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0130/config.yaml",
)
