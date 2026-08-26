"""Workbook strategy xlsx_s2_0820; source provenance is in config.yaml."""

from strategies.xlsx_s2_0820.config import XlsxS20820Config
from strategies.xlsx_s2_0820.plugin import PLUGIN
from strategies.xlsx_s2_0820.strategy import XlsxS20820Strategy

__all__ = ["PLUGIN", "XlsxS20820Config", "XlsxS20820Strategy"]
