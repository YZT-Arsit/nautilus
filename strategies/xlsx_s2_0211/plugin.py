"""Normal StrategyPlugin registration seam for xlsx_s2_0211."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0211.config import XlsxS20211Config
from strategies.xlsx_s2_0211.strategy import XlsxS20211Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0211",
    config_cls=XlsxS20211Config,
    strategy_cls=XlsxS20211Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0211/config.yaml",
)
