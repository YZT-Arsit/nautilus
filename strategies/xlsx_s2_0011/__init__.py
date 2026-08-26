"""Workbook strategy xlsx_s2_0011; source provenance is in config.yaml."""

from strategies.xlsx_s2_0011.config import XlsxS20011Config
from strategies.xlsx_s2_0011.plugin import PLUGIN
from strategies.xlsx_s2_0011.strategy import XlsxS20011Strategy

__all__ = ["PLUGIN", "XlsxS20011Config", "XlsxS20011Strategy"]
