"""Workbook strategy xlsx_s2_0525; source provenance is in config.yaml."""

from strategies.xlsx_s2_0525.config import XlsxS20525Config
from strategies.xlsx_s2_0525.plugin import PLUGIN
from strategies.xlsx_s2_0525.strategy import XlsxS20525Strategy

__all__ = ["PLUGIN", "XlsxS20525Config", "XlsxS20525Strategy"]
