"""Workbook strategy xlsx_s2_0882; source provenance is in config.yaml."""

from strategies.xlsx_s2_0882.config import XlsxS20882Config
from strategies.xlsx_s2_0882.plugin import PLUGIN
from strategies.xlsx_s2_0882.strategy import XlsxS20882Strategy

__all__ = ["PLUGIN", "XlsxS20882Config", "XlsxS20882Strategy"]
