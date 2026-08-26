"""Workbook strategy xlsx_s2_0125; source provenance is in config.yaml."""

from strategies.xlsx_s2_0125.config import XlsxS20125Config
from strategies.xlsx_s2_0125.plugin import PLUGIN
from strategies.xlsx_s2_0125.strategy import XlsxS20125Strategy

__all__ = ["PLUGIN", "XlsxS20125Config", "XlsxS20125Strategy"]
