"""Normal StrategyPlugin registration seam for xlsx_s2_0433."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0433.config import XlsxS20433Config
from strategies.xlsx_s2_0433.strategy import XlsxS20433Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0433",
    config_cls=XlsxS20433Config,
    strategy_cls=XlsxS20433Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0433/config.yaml",
)
