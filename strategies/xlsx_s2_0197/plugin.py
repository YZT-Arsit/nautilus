"""Normal StrategyPlugin registration seam for xlsx_s2_0197."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0197.config import XlsxS20197Config
from strategies.xlsx_s2_0197.strategy import XlsxS20197Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0197",
    config_cls=XlsxS20197Config,
    strategy_cls=XlsxS20197Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0197/config.yaml",
)
