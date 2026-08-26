"""Normal StrategyPlugin registration seam for xlsx_s2_0792."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0792.config import XlsxS20792Config
from strategies.xlsx_s2_0792.strategy import XlsxS20792Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0792",
    config_cls=XlsxS20792Config,
    strategy_cls=XlsxS20792Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0792/config.yaml",
)
