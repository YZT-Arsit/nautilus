"""Workbook strategy xlsx_s2_0121; source provenance is in config.yaml."""

from strategies.xlsx_s2_0121.config import XlsxS20121Config
from strategies.xlsx_s2_0121.plugin import PLUGIN
from strategies.xlsx_s2_0121.strategy import XlsxS20121Strategy

__all__ = ["PLUGIN", "XlsxS20121Config", "XlsxS20121Strategy"]
