"""Workbook strategy xlsx_s2_0812; source provenance is in config.yaml."""

from strategies.xlsx_s2_0812.config import XlsxS20812Config
from strategies.xlsx_s2_0812.plugin import PLUGIN
from strategies.xlsx_s2_0812.strategy import XlsxS20812Strategy

__all__ = ["PLUGIN", "XlsxS20812Config", "XlsxS20812Strategy"]
