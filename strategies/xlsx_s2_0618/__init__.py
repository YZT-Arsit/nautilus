"""Workbook strategy xlsx_s2_0618; source provenance is in config.yaml."""

from strategies.xlsx_s2_0618.config import XlsxS20618Config
from strategies.xlsx_s2_0618.plugin import PLUGIN
from strategies.xlsx_s2_0618.strategy import XlsxS20618Strategy

__all__ = ["PLUGIN", "XlsxS20618Config", "XlsxS20618Strategy"]
