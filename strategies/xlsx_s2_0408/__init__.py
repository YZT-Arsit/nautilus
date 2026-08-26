"""Workbook strategy xlsx_s2_0408; source provenance is in config.yaml."""

from strategies.xlsx_s2_0408.config import XlsxS20408Config
from strategies.xlsx_s2_0408.plugin import PLUGIN
from strategies.xlsx_s2_0408.strategy import XlsxS20408Strategy

__all__ = ["PLUGIN", "XlsxS20408Config", "XlsxS20408Strategy"]
