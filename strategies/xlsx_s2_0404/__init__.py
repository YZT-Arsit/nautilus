"""Workbook strategy xlsx_s2_0404; source provenance is in config.yaml."""

from strategies.xlsx_s2_0404.config import XlsxS20404Config
from strategies.xlsx_s2_0404.plugin import PLUGIN
from strategies.xlsx_s2_0404.strategy import XlsxS20404Strategy

__all__ = ["PLUGIN", "XlsxS20404Config", "XlsxS20404Strategy"]
