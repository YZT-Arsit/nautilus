"""Workbook strategy xlsx_s2_0601; source provenance is in config.yaml."""

from strategies.xlsx_s2_0601.config import XlsxS20601Config
from strategies.xlsx_s2_0601.plugin import PLUGIN
from strategies.xlsx_s2_0601.strategy import XlsxS20601Strategy

__all__ = ["PLUGIN", "XlsxS20601Config", "XlsxS20601Strategy"]
