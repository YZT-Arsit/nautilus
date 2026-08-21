"""Normal StrategyPlugin registration seam for xlsx_s2_0436."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0436.config import XlsxS20436Config
from strategies.xlsx_s2_0436.strategy import XlsxS20436Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0436",
    config_cls=XlsxS20436Config,
    strategy_cls=XlsxS20436Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0436/config.yaml",
)
