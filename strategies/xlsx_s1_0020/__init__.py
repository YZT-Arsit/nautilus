"""Workbook strategy xlsx_s1_0020; source provenance is in config.yaml."""

from strategies.xlsx_s1_0020.config import XlsxS10020Config
from strategies.xlsx_s1_0020.plugin import PLUGIN
from strategies.xlsx_s1_0020.strategy import XlsxS10020Strategy

__all__ = ["PLUGIN", "XlsxS10020Config", "XlsxS10020Strategy"]
