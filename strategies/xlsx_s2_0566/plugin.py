"""Normal StrategyPlugin registration seam for xlsx_s2_0566."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0566.config import XlsxS20566Config
from strategies.xlsx_s2_0566.strategy import XlsxS20566Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0566",
    config_cls=XlsxS20566Config,
    strategy_cls=XlsxS20566Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0566/config.yaml",
)
