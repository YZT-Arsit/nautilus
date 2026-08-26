"""Workbook strategy xlsx_s2_0705; source provenance is in config.yaml."""

from strategies.xlsx_s2_0705.config import XlsxS20705Config
from strategies.xlsx_s2_0705.plugin import PLUGIN
from strategies.xlsx_s2_0705.strategy import XlsxS20705Strategy

__all__ = ["PLUGIN", "XlsxS20705Config", "XlsxS20705Strategy"]
