"""Normal StrategyPlugin registration seam for xlsx_s2_0418."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0418.config import XlsxS20418Config
from strategies.xlsx_s2_0418.strategy import XlsxS20418Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0418",
    config_cls=XlsxS20418Config,
    strategy_cls=XlsxS20418Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0418/config.yaml",
)
