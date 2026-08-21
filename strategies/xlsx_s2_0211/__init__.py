"""Workbook strategy xlsx_s2_0211; source provenance is in config.yaml."""

from strategies.xlsx_s2_0211.config import XlsxS20211Config
from strategies.xlsx_s2_0211.plugin import PLUGIN
from strategies.xlsx_s2_0211.strategy import XlsxS20211Strategy

__all__ = ["PLUGIN", "XlsxS20211Config", "XlsxS20211Strategy"]
