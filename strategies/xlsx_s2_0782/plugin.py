"""Normal StrategyPlugin registration seam for xlsx_s2_0782."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0782.config import XlsxS20782Config
from strategies.xlsx_s2_0782.strategy import XlsxS20782Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0782",
    config_cls=XlsxS20782Config,
    strategy_cls=XlsxS20782Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0782/config.yaml",
)
