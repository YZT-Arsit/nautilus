"""Workbook strategy xlsx_s1_0004; source provenance is in config.yaml."""

from strategies.xlsx_s1_0004.config import XlsxS10004Config
from strategies.xlsx_s1_0004.plugin import PLUGIN
from strategies.xlsx_s1_0004.strategy import XlsxS10004Strategy

__all__ = ["PLUGIN", "XlsxS10004Config", "XlsxS10004Strategy"]
