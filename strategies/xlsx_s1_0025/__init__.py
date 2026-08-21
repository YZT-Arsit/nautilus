"""Workbook strategy xlsx_s1_0025; source provenance is in config.yaml."""

from strategies.xlsx_s1_0025.config import XlsxS10025Config
from strategies.xlsx_s1_0025.plugin import PLUGIN
from strategies.xlsx_s1_0025.strategy import XlsxS10025Strategy

__all__ = ["PLUGIN", "XlsxS10025Config", "XlsxS10025Strategy"]
