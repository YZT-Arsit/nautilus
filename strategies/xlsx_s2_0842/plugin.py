"""Normal StrategyPlugin registration seam for xlsx_s2_0842."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0842.config import XlsxS20842Config
from strategies.xlsx_s2_0842.strategy import XlsxS20842Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0842",
    config_cls=XlsxS20842Config,
    strategy_cls=XlsxS20842Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0842/config.yaml",
)
