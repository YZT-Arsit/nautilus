"""Normal StrategyPlugin registration seam for xlsx_s2_0809."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0809.config import XlsxS20809Config
from strategies.xlsx_s2_0809.strategy import XlsxS20809Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0809",
    config_cls=XlsxS20809Config,
    strategy_cls=XlsxS20809Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0809/config.yaml",
)
