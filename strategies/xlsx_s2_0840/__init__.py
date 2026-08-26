"""Workbook strategy xlsx_s2_0840; source provenance is in config.yaml."""

from strategies.xlsx_s2_0840.config import XlsxS20840Config
from strategies.xlsx_s2_0840.plugin import PLUGIN
from strategies.xlsx_s2_0840.strategy import XlsxS20840Strategy

__all__ = ["PLUGIN", "XlsxS20840Config", "XlsxS20840Strategy"]
