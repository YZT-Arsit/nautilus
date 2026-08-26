"""Normal StrategyPlugin registration seam for xlsx_s2_0727."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0727.config import XlsxS20727Config
from strategies.xlsx_s2_0727.strategy import XlsxS20727Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0727",
    config_cls=XlsxS20727Config,
    strategy_cls=XlsxS20727Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0727/config.yaml",
)
