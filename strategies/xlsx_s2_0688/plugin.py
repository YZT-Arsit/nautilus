"""Normal StrategyPlugin registration seam for xlsx_s2_0688."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0688.config import XlsxS20688Config
from strategies.xlsx_s2_0688.strategy import XlsxS20688Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0688",
    config_cls=XlsxS20688Config,
    strategy_cls=XlsxS20688Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0688/config.yaml",
)
