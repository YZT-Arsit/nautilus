"""Normal StrategyPlugin registration seam for xlsx_s2_0042."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0042.config import XlsxS20042Config
from strategies.xlsx_s2_0042.strategy import XlsxS20042Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0042",
    config_cls=XlsxS20042Config,
    strategy_cls=XlsxS20042Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0042/config.yaml",
)
