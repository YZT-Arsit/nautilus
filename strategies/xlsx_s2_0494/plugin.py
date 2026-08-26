"""Normal StrategyPlugin registration seam for xlsx_s2_0494."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0494.config import XlsxS20494Config
from strategies.xlsx_s2_0494.strategy import XlsxS20494Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0494",
    config_cls=XlsxS20494Config,
    strategy_cls=XlsxS20494Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0494/config.yaml",
)
