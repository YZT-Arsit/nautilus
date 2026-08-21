"""Workbook strategy xlsx_s2_0315; source provenance is in config.yaml."""

from strategies.xlsx_s2_0315.config import XlsxS20315Config
from strategies.xlsx_s2_0315.plugin import PLUGIN
from strategies.xlsx_s2_0315.strategy import XlsxS20315Strategy

__all__ = ["PLUGIN", "XlsxS20315Config", "XlsxS20315Strategy"]
