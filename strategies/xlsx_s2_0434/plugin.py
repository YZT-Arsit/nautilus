"""Normal StrategyPlugin registration seam for xlsx_s2_0434."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0434.config import XlsxS20434Config
from strategies.xlsx_s2_0434.strategy import XlsxS20434Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0434",
    config_cls=XlsxS20434Config,
    strategy_cls=XlsxS20434Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0434/config.yaml",
)
