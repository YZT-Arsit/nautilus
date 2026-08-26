"""Workbook strategy xlsx_s2_0783; source provenance is in config.yaml."""

from strategies.xlsx_s2_0783.config import XlsxS20783Config
from strategies.xlsx_s2_0783.plugin import PLUGIN
from strategies.xlsx_s2_0783.strategy import XlsxS20783Strategy

__all__ = ["PLUGIN", "XlsxS20783Config", "XlsxS20783Strategy"]
