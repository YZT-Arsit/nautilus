"""Workbook strategy xlsx_s1_0012; source provenance is in config.yaml."""

from strategies.xlsx_s1_0012.config import XlsxS10012Config
from strategies.xlsx_s1_0012.plugin import PLUGIN
from strategies.xlsx_s1_0012.strategy import XlsxS10012Strategy

__all__ = ["PLUGIN", "XlsxS10012Config", "XlsxS10012Strategy"]
