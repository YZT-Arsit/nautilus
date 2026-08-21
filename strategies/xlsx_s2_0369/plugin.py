"""Normal StrategyPlugin registration seam for xlsx_s2_0369."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0369.config import XlsxS20369Config
from strategies.xlsx_s2_0369.strategy import XlsxS20369Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0369",
    config_cls=XlsxS20369Config,
    strategy_cls=XlsxS20369Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0369/config.yaml",
)
