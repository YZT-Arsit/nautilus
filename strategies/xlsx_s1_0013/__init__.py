"""Workbook strategy xlsx_s1_0013; source provenance is in config.yaml."""

from strategies.xlsx_s1_0013.config import XlsxS10013Config
from strategies.xlsx_s1_0013.plugin import PLUGIN
from strategies.xlsx_s1_0013.strategy import XlsxS10013Strategy

__all__ = ["PLUGIN", "XlsxS10013Config", "XlsxS10013Strategy"]
