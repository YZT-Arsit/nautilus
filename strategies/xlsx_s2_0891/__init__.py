"""Workbook strategy xlsx_s2_0891; source provenance is in config.yaml."""

from strategies.xlsx_s2_0891.config import XlsxS20891Config
from strategies.xlsx_s2_0891.plugin import PLUGIN
from strategies.xlsx_s2_0891.strategy import XlsxS20891Strategy

__all__ = ["PLUGIN", "XlsxS20891Config", "XlsxS20891Strategy"]
