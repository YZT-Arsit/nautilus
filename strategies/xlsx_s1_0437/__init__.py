"""Workbook strategy xlsx_s1_0437; source provenance is in config.yaml."""

from strategies.xlsx_s1_0437.config import XlsxS10437Config
from strategies.xlsx_s1_0437.plugin import PLUGIN
from strategies.xlsx_s1_0437.strategy import XlsxS10437Strategy

__all__ = ["PLUGIN", "XlsxS10437Config", "XlsxS10437Strategy"]
