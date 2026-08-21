"""Workbook strategy xlsx_s2_0503; source provenance is in config.yaml."""

from strategies.xlsx_s2_0503.config import XlsxS20503Config
from strategies.xlsx_s2_0503.plugin import PLUGIN
from strategies.xlsx_s2_0503.strategy import XlsxS20503Strategy

__all__ = ["PLUGIN", "XlsxS20503Config", "XlsxS20503Strategy"]
