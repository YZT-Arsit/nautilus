"""Normal StrategyPlugin registration seam for xlsx_s2_0022."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0022.config import XlsxS20022Config
from strategies.xlsx_s2_0022.strategy import XlsxS20022Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0022",
    config_cls=XlsxS20022Config,
    strategy_cls=XlsxS20022Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0022/config.yaml",
)
