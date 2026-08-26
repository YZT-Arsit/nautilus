"""Workbook strategy xlsx_s2_0627; source provenance is in config.yaml."""

from strategies.xlsx_s2_0627.config import XlsxS20627Config
from strategies.xlsx_s2_0627.plugin import PLUGIN
from strategies.xlsx_s2_0627.strategy import XlsxS20627Strategy

__all__ = ["PLUGIN", "XlsxS20627Config", "XlsxS20627Strategy"]
