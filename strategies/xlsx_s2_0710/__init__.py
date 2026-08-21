"""Workbook strategy xlsx_s2_0710; source provenance is in config.yaml."""

from strategies.xlsx_s2_0710.config import XlsxS20710Config
from strategies.xlsx_s2_0710.plugin import PLUGIN
from strategies.xlsx_s2_0710.strategy import XlsxS20710Strategy

__all__ = ["PLUGIN", "XlsxS20710Config", "XlsxS20710Strategy"]
