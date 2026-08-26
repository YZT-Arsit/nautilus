"""Workbook strategy xlsx_s2_0550; source provenance is in config.yaml."""

from strategies.xlsx_s2_0550.config import XlsxS20550Config
from strategies.xlsx_s2_0550.plugin import PLUGIN
from strategies.xlsx_s2_0550.strategy import XlsxS20550Strategy

__all__ = ["PLUGIN", "XlsxS20550Config", "XlsxS20550Strategy"]
