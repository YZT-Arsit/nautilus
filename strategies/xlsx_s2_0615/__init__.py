"""Workbook strategy xlsx_s2_0615; source provenance is in config.yaml."""

from strategies.xlsx_s2_0615.config import XlsxS20615Config
from strategies.xlsx_s2_0615.plugin import PLUGIN
from strategies.xlsx_s2_0615.strategy import XlsxS20615Strategy

__all__ = ["PLUGIN", "XlsxS20615Config", "XlsxS20615Strategy"]
