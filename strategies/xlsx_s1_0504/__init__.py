"""Workbook strategy xlsx_s1_0504; source provenance is in config.yaml."""

from strategies.xlsx_s1_0504.config import XlsxS10504Config
from strategies.xlsx_s1_0504.plugin import PLUGIN
from strategies.xlsx_s1_0504.strategy import XlsxS10504Strategy

__all__ = ["PLUGIN", "XlsxS10504Config", "XlsxS10504Strategy"]
