"""Workbook strategy xlsx_s1_0002; source provenance is in config.yaml."""

from strategies.xlsx_s1_0002.config import XlsxS10002Config
from strategies.xlsx_s1_0002.plugin import PLUGIN
from strategies.xlsx_s1_0002.strategy import XlsxS10002Strategy

__all__ = ["PLUGIN", "XlsxS10002Config", "XlsxS10002Strategy"]
