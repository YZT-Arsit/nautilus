"""Normal StrategyPlugin registration seam for xlsx_s2_0747."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0747.config import XlsxS20747Config
from strategies.xlsx_s2_0747.strategy import XlsxS20747Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0747",
    config_cls=XlsxS20747Config,
    strategy_cls=XlsxS20747Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0747/config.yaml",
)
