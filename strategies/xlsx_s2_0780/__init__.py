"""Workbook strategy xlsx_s2_0780; source provenance is in config.yaml."""

from strategies.xlsx_s2_0780.config import XlsxS20780Config
from strategies.xlsx_s2_0780.plugin import PLUGIN
from strategies.xlsx_s2_0780.strategy import XlsxS20780Strategy

__all__ = ["PLUGIN", "XlsxS20780Config", "XlsxS20780Strategy"]
