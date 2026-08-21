"""Normal StrategyPlugin registration seam for xlsx_s2_0261."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0261.config import XlsxS20261Config
from strategies.xlsx_s2_0261.strategy import XlsxS20261Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0261",
    config_cls=XlsxS20261Config,
    strategy_cls=XlsxS20261Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0261/config.yaml",
)
