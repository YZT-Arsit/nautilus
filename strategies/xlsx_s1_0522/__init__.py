"""Workbook strategy xlsx_s1_0522; source provenance is in config.yaml."""

from strategies.xlsx_s1_0522.config import XlsxS10522Config
from strategies.xlsx_s1_0522.plugin import PLUGIN
from strategies.xlsx_s1_0522.strategy import XlsxS10522Strategy

__all__ = ["PLUGIN", "XlsxS10522Config", "XlsxS10522Strategy"]
