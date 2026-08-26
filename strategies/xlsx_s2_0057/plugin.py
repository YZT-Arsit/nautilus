"""Normal StrategyPlugin registration seam for xlsx_s2_0057."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0057.config import XlsxS20057Config
from strategies.xlsx_s2_0057.strategy import XlsxS20057Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0057",
    config_cls=XlsxS20057Config,
    strategy_cls=XlsxS20057Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0057/config.yaml",
)
