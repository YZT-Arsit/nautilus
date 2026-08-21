"""Normal StrategyPlugin registration seam for xlsx_s2_0795."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0795.config import XlsxS20795Config
from strategies.xlsx_s2_0795.strategy import XlsxS20795Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0795",
    config_cls=XlsxS20795Config,
    strategy_cls=XlsxS20795Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0795/config.yaml",
)
