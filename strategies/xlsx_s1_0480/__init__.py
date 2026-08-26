"""Workbook strategy xlsx_s1_0480; source provenance is in config.yaml."""

from strategies.xlsx_s1_0480.config import XlsxS10480Config
from strategies.xlsx_s1_0480.plugin import PLUGIN
from strategies.xlsx_s1_0480.strategy import XlsxS10480Strategy

__all__ = ["PLUGIN", "XlsxS10480Config", "XlsxS10480Strategy"]
