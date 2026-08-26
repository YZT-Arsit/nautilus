"""Workbook strategy xlsx_s1_0438; source provenance is in config.yaml."""

from strategies.xlsx_s1_0438.config import XlsxS10438Config
from strategies.xlsx_s1_0438.plugin import PLUGIN
from strategies.xlsx_s1_0438.strategy import XlsxS10438Strategy

__all__ = ["PLUGIN", "XlsxS10438Config", "XlsxS10438Strategy"]
