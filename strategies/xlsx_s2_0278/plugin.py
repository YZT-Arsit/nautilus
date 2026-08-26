"""Normal StrategyPlugin registration seam for xlsx_s2_0278."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0278.config import XlsxS20278Config
from strategies.xlsx_s2_0278.strategy import XlsxS20278Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0278",
    config_cls=XlsxS20278Config,
    strategy_cls=XlsxS20278Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0278/config.yaml",
)
