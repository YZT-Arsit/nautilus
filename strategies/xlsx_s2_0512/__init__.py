"""Workbook strategy xlsx_s2_0512; source provenance is in config.yaml."""

from strategies.xlsx_s2_0512.config import XlsxS20512Config
from strategies.xlsx_s2_0512.plugin import PLUGIN
from strategies.xlsx_s2_0512.strategy import XlsxS20512Strategy

__all__ = ["PLUGIN", "XlsxS20512Config", "XlsxS20512Strategy"]
