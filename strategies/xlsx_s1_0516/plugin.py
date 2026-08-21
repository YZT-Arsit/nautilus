"""Normal StrategyPlugin registration seam for xlsx_s1_0516."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0516.config import XlsxS10516Config
from strategies.xlsx_s1_0516.strategy import XlsxS10516Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0516",
    config_cls=XlsxS10516Config,
    strategy_cls=XlsxS10516Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0516/config.yaml",
)
