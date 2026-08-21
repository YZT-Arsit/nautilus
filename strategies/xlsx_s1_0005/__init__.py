"""Workbook strategy xlsx_s1_0005; source provenance is in config.yaml."""

from strategies.xlsx_s1_0005.config import XlsxS10005Config
from strategies.xlsx_s1_0005.plugin import PLUGIN
from strategies.xlsx_s1_0005.strategy import XlsxS10005Strategy

__all__ = ["PLUGIN", "XlsxS10005Config", "XlsxS10005Strategy"]
