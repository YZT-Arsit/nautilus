"""Workbook strategy xlsx_s2_0788; source provenance is in config.yaml."""

from strategies.xlsx_s2_0788.config import XlsxS20788Config
from strategies.xlsx_s2_0788.plugin import PLUGIN
from strategies.xlsx_s2_0788.strategy import XlsxS20788Strategy

__all__ = ["PLUGIN", "XlsxS20788Config", "XlsxS20788Strategy"]
