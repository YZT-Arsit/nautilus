"""Workbook strategy xlsx_s2_0438; source provenance is in config.yaml."""

from strategies.xlsx_s2_0438.config import XlsxS20438Config
from strategies.xlsx_s2_0438.plugin import PLUGIN
from strategies.xlsx_s2_0438.strategy import XlsxS20438Strategy

__all__ = ["PLUGIN", "XlsxS20438Config", "XlsxS20438Strategy"]
