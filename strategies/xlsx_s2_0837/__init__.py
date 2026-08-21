"""Workbook strategy xlsx_s2_0837; source provenance is in config.yaml."""

from strategies.xlsx_s2_0837.config import XlsxS20837Config
from strategies.xlsx_s2_0837.plugin import PLUGIN
from strategies.xlsx_s2_0837.strategy import XlsxS20837Strategy

__all__ = ["PLUGIN", "XlsxS20837Config", "XlsxS20837Strategy"]
