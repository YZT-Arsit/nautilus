"""Workbook strategy xlsx_s2_0504; source provenance is in config.yaml."""

from strategies.xlsx_s2_0504.config import XlsxS20504Config
from strategies.xlsx_s2_0504.plugin import PLUGIN
from strategies.xlsx_s2_0504.strategy import XlsxS20504Strategy

__all__ = ["PLUGIN", "XlsxS20504Config", "XlsxS20504Strategy"]
