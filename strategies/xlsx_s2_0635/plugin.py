"""Normal StrategyPlugin registration seam for xlsx_s2_0635."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0635.config import XlsxS20635Config
from strategies.xlsx_s2_0635.strategy import XlsxS20635Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0635",
    config_cls=XlsxS20635Config,
    strategy_cls=XlsxS20635Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0635/config.yaml",
)
