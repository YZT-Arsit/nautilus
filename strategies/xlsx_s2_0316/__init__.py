"""Workbook strategy xlsx_s2_0316; source provenance is in config.yaml."""

from strategies.xlsx_s2_0316.config import XlsxS20316Config
from strategies.xlsx_s2_0316.plugin import PLUGIN
from strategies.xlsx_s2_0316.strategy import XlsxS20316Strategy

__all__ = ["PLUGIN", "XlsxS20316Config", "XlsxS20316Strategy"]
