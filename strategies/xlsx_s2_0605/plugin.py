"""Normal StrategyPlugin registration seam for xlsx_s2_0605."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0605.config import XlsxS20605Config
from strategies.xlsx_s2_0605.strategy import XlsxS20605Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0605",
    config_cls=XlsxS20605Config,
    strategy_cls=XlsxS20605Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0605/config.yaml",
)
