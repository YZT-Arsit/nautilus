"""Workbook strategy xlsx_s2_0725; source provenance is in config.yaml."""

from strategies.xlsx_s2_0725.config import XlsxS20725Config
from strategies.xlsx_s2_0725.plugin import PLUGIN
from strategies.xlsx_s2_0725.strategy import XlsxS20725Strategy

__all__ = ["PLUGIN", "XlsxS20725Config", "XlsxS20725Strategy"]
