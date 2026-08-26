"""Workbook strategy xlsx_s1_0456; source provenance is in config.yaml."""

from strategies.xlsx_s1_0456.config import XlsxS10456Config
from strategies.xlsx_s1_0456.plugin import PLUGIN
from strategies.xlsx_s1_0456.strategy import XlsxS10456Strategy

__all__ = ["PLUGIN", "XlsxS10456Config", "XlsxS10456Strategy"]
