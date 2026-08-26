"""Workbook strategy xlsx_s2_0418; source provenance is in config.yaml."""

from strategies.xlsx_s2_0418.config import XlsxS20418Config
from strategies.xlsx_s2_0418.plugin import PLUGIN
from strategies.xlsx_s2_0418.strategy import XlsxS20418Strategy

__all__ = ["PLUGIN", "XlsxS20418Config", "XlsxS20418Strategy"]
