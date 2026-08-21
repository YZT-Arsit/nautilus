"""Normal StrategyPlugin registration seam for xlsx_s2_0344."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0344.config import XlsxS20344Config
from strategies.xlsx_s2_0344.strategy import XlsxS20344Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0344",
    config_cls=XlsxS20344Config,
    strategy_cls=XlsxS20344Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0344/config.yaml",
)
