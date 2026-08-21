"""Workbook strategy xlsx_s1_0010; source provenance is in config.yaml."""

from strategies.xlsx_s1_0010.config import XlsxS10010Config
from strategies.xlsx_s1_0010.plugin import PLUGIN
from strategies.xlsx_s1_0010.strategy import XlsxS10010Strategy

__all__ = ["PLUGIN", "XlsxS10010Config", "XlsxS10010Strategy"]
