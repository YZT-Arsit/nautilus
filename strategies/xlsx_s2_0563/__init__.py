"""Workbook strategy xlsx_s2_0563; source provenance is in config.yaml."""

from strategies.xlsx_s2_0563.config import XlsxS20563Config
from strategies.xlsx_s2_0563.plugin import PLUGIN
from strategies.xlsx_s2_0563.strategy import XlsxS20563Strategy

__all__ = ["PLUGIN", "XlsxS20563Config", "XlsxS20563Strategy"]
