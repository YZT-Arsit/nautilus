"""Workbook strategy xlsx_s1_0440; source provenance is in config.yaml."""

from strategies.xlsx_s1_0440.config import XlsxS10440Config
from strategies.xlsx_s1_0440.plugin import PLUGIN
from strategies.xlsx_s1_0440.strategy import XlsxS10440Strategy

__all__ = ["PLUGIN", "XlsxS10440Config", "XlsxS10440Strategy"]
