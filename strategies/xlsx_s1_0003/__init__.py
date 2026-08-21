"""Workbook strategy xlsx_s1_0003; source provenance is in config.yaml."""

from strategies.xlsx_s1_0003.config import XlsxS10003Config
from strategies.xlsx_s1_0003.plugin import PLUGIN
from strategies.xlsx_s1_0003.strategy import XlsxS10003Strategy

__all__ = ["PLUGIN", "XlsxS10003Config", "XlsxS10003Strategy"]
